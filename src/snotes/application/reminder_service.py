"""Application owner for Reminder R1 lifecycle and notification decisions."""

from __future__ import annotations

import hashlib
import json
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
    DEFAULT_REMINDER_TABLE_TITLE,
    NOTIFICATION_ACTION,
    NOTIFICATION_DEADLINE,
    OCCURRENCE_COMPLETED,
    OCCURRENCE_PENDING,
    REMINDER_ACTIVE,
    REMINDER_COMPLETED,
    REMINDER_DELETED,
    REMINDER_PAUSED,
    Reminder,
    ReminderDraft,
    ReminderLabel,
    ReminderListItem,
    ReminderNotification,
    ReminderOperationPage,
    ReminderOccurrence,
    ReminderQuery,
    ReminderSettings,
    ReminderTable,
    ReminderTableListItem,
    ReminderTrashSnapshot,
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
        cleaned = self._clean_draft(draft, existing=existing)
        updated = self._repository.update(reminder_id, cleaned)
        if (
            existing.action_spec != updated.action_spec
            or existing.deadline_spec != updated.deadline_spec
        ):
            self._repository.delete_pending_occurrences(reminder_id)
        return updated

    def delete_reminder(self, reminder_id: int) -> None:
        self._repository.delete(reminder_id)

    def restore_reminder(self, reminder_id: int) -> Reminder:
        raise ValueError("reminder_item_trash_not_supported")

    def permanently_delete_reminder(self, reminder_id: int) -> None:
        self._repository.permanently_delete_trashed(reminder_id)

    def list_trash(self) -> ReminderTrashSnapshot:
        return self._repository.list_trash()

    def pause_reminder(self, reminder_id: int) -> None:
        self._repository.set_lifecycle(reminder_id, REMINDER_PAUSED)

    def resume_reminder(self, reminder_id: int) -> None:
        reminder = self._required(reminder_id)
        if reminder.lifecycle == REMINDER_COMPLETED:
            raise ValueError("completed_reminder_cannot_resume")
        self._repository.set_lifecycle(reminder_id, REMINDER_ACTIVE)

    def complete_reminder(self, reminder_id: int) -> None:
        now = self._timestamp()
        item = self.get_reminder(reminder_id)
        if item is None:
            raise ValueError("reminder_not_found")
        if item.occurrence is not None:
            self._repository.mark_occurrence_completed(item.occurrence.id, now)
        self._repository.set_lifecycle(reminder_id, REMINDER_COMPLETED, now)

    def complete_occurrence(self, reminder_id: int) -> None:
        now = self._timestamp()
        item = self.get_reminder(reminder_id)
        if item is None:
            return
        if item.occurrence is None:
            return
        self._repository.mark_occurrence_completed(item.occurrence.id, now)

    def list_reminders(self, query: ReminderQuery | None = None) -> list[ReminderListItem]:
        filters = query or ReminderQuery()
        today = today_shanghai(self._now_datetime)
        labels = {label.id: label for label in self._repository.list_labels(include_deleted=True)}
        tables = {
            table.id: table
            for table in self._repository.list_tables(
                include_deleted=filters.include_deleted_tables
            )
        }
        items: list[ReminderListItem] = []
        for reminder in self._repository.list_records(
            include_deleted=filters.include_deleted_tables,
            table_id=filters.table_id,
        ):
            table = tables.get(reminder.table_id)
            if table is None:
                continue
            if not filters.include_deleted_tables and table.is_deleted:
                continue
            if filters.ai_access and not table.ai_visible:
                continue
            if filters.label_id is not None and table.label_id != filters.label_id:
                continue
            if filters.ai_visibility == "visible" and not table.ai_visible:
                continue
            if filters.ai_visibility == "hidden" and table.ai_visible:
                continue
            try:
                item = self._list_item(
                    reminder,
                    table,
                    labels.get(table.label_id) or self._repository.default_label(),
                    today,
                )
            except ValueError as error:
                if _is_unsupported_schedule_kind(error):
                    continue
                raise
            if filters.lifecycle is not None and item.status != filters.lifecycle and reminder.lifecycle != filters.lifecycle:
                continue
            if not self._matches_query(item, filters):
                continue
            items.append(item)
        return sorted(items, key=self._sort_key)

    def get_reminder(
        self,
        reminder_id: int,
        *,
        ai_access: bool = False,
        include_deleted: bool = False,
    ) -> ReminderListItem | None:
        reminder = self._repository.get(reminder_id, include_deleted=include_deleted)
        if reminder is None:
            return None
        table = self._repository.get_table(
            reminder.table_id,
            include_deleted=include_deleted,
        )
        if table is None or (ai_access and not table.ai_visible):
            return None
        label = self._repository.get_label(table.label_id, include_deleted=True)
        try:
            return self._list_item(
                reminder,
                table,
                label or self._repository.default_label(),
                today_shanghai(self._now_datetime),
            )
        except ValueError as error:
            if _is_unsupported_schedule_kind(error):
                return None
            raise

    def list_labels(self, *, include_deleted: bool = False) -> list[ReminderLabel]:
        return self._repository.list_labels(include_deleted=include_deleted)

    def default_label(self) -> ReminderLabel:
        return self._repository.default_label()

    def create_label(self, name: str) -> ReminderLabel:
        return self._repository.create_label(name)

    def rename_label(self, label_id: int, name: str) -> ReminderLabel:
        label = self._repository.get_label(label_id)
        if label is None:
            raise ValueError("reminder_label_not_found")
        return self._repository.rename_label(label_id, name)

    def reorder_labels(self, label_ids: Sequence[int]) -> list[ReminderLabel]:
        return self._repository.reorder_labels(label_ids)

    def delete_label(self, label_id: int) -> None:
        self._repository.soft_delete_label_with_tables(label_id)

    def default_table(self) -> ReminderTable:
        return self._repository.default_table()

    def list_tables(self, query: ReminderQuery | None = None) -> list[ReminderTableListItem]:
        filters = query or ReminderQuery()
        labels = {label.id: label for label in self._repository.list_labels(include_deleted=True)}
        items: list[ReminderTableListItem] = []
        for table in self._repository.list_tables(
            include_deleted=filters.include_deleted_tables
        ):
            if not filters.include_deleted_tables and table.is_deleted:
                continue
            if filters.ai_access and not table.ai_visible:
                continue
            if filters.label_id is not None and table.label_id != filters.label_id:
                continue
            if filters.ai_visibility == "visible" and not table.ai_visible:
                continue
            if filters.ai_visibility == "hidden" and table.ai_visible:
                continue
            label = labels.get(table.label_id) or self._repository.default_label()
            reminders = self.list_reminders(
                ReminderQuery(
                    query="",
                    table_id=table.id,
                    include_deleted_tables=filters.include_deleted_tables,
                    ai_access=filters.ai_access,
                )
            )
            match_count, matching_item_id = self._table_match(table, label, reminders, filters.query)
            if filters.query.strip() and match_count == 0:
                continue
            items.append(
                ReminderTableListItem(
                    table=table,
                    label=label,
                    item_count=len(reminders),
                    matching_item_id=matching_item_id,
                    match_count=match_count,
                )
            )
        return sorted(items, key=lambda item: (item.table.sort_order, item.table.title.casefold(), item.table.id))

    def get_table(
        self,
        table_id: int,
        *,
        ai_access: bool = False,
        include_deleted: bool = False,
    ) -> ReminderTableListItem | None:
        table = self._repository.get_table(table_id, include_deleted=include_deleted)
        if table is None or (ai_access and not table.ai_visible):
            return None
        label = self._repository.get_label(table.label_id, include_deleted=True)
        reminders = self.list_reminders(
            ReminderQuery(
                table_id=table.id,
                include_deleted_tables=include_deleted,
                ai_access=ai_access,
            )
        )
        return ReminderTableListItem(
            table,
            label or self._repository.default_label(),
            len(reminders),
        )

    def create_table(
        self,
        title: str | None = None,
        *,
        label_id: int | None = None,
        ai_visible: bool = True,
    ) -> ReminderTable:
        clean_title = title.strip() if title else self._automatic_table_title()
        return self._repository.create_table(
            title=clean_title,
            label_id=label_id,
            ai_visible=ai_visible,
        )

    def update_table_metadata(
        self,
        table_id: int,
        *,
        title: str | None = None,
        label_id: int | None = None,
    ) -> ReminderTable:
        return self._repository.update_table_metadata(
            table_id,
            title=title,
            label_id=label_id,
        )

    def set_table_ai_visible(self, table_id: int, ai_visible: bool) -> ReminderTable:
        return self._repository.set_table_ai_visible(table_id, ai_visible)

    def delete_table(self, table_id: int) -> None:
        self._repository.soft_delete_table(table_id)

    def restore_table(self, table_id: int) -> ReminderTable:
        return self._repository.restore_table(table_id)

    def permanently_delete_table(self, table_id: int) -> None:
        self._repository.permanently_delete_table(table_id)

    def list_ai_operation_logs(
        self,
        *,
        offset: int = 0,
        limit: int = 50,
    ) -> ReminderOperationPage:
        return self._repository.list_ai_operation_logs(offset=offset, limit=limit)

    def ai_create_label(
        self,
        *,
        actor_target_id: str,
        request_id: str,
        name: str,
    ) -> dict:
        existing = self._repository.get_operation(request_id, actor_target_id)
        if existing is not None:
            return {**existing, "status": "already_processed", "previous_status": existing.get("status")}
        before_names = {label.name.casefold() for label in self.list_labels()}
        label = self.create_label(name)
        result = _mcp_result(
            "created" if label.name.casefold() not in before_names else "no_change",
            request_id,
            table_id=None,
            item_id=None,
            title=label.name,
        )
        stored = self._repository.store_operation(
            actor_target_id=actor_target_id,
            operation_type="create_reminder_label",
            result=result,
        )
        if result["status"] == "created":
            self._repository.record_ai_operation(
                actor_target_id=actor_target_id,
                action="create_label",
                table_id=None,
                table_title="",
                item_id=None,
                item_title="",
                label_id=label.id,
                label_name=label.name,
                request_id=request_id,
                changed_fields=("label",),
                before={},
                after={"label_id": label.id, "label": label.name},
            )
        return stored

    def ai_create_table(
        self,
        *,
        actor_target_id: str,
        request_id: str,
        title: str | None = None,
        label_id: int | None = None,
    ) -> dict:
        existing = self._repository.get_operation(request_id, actor_target_id)
        if existing is not None:
            return {**existing, "status": "already_processed", "previous_status": existing.get("status")}
        table = self.create_table(title, label_id=label_id, ai_visible=True)
        item = self.get_table(table.id)
        result = _mcp_result("created", request_id, table_id=table.id, item_id=None, title=table.title)
        stored = self._repository.store_operation(
            actor_target_id=actor_target_id,
            operation_type="create_reminder_table",
            result=result,
        )
        if item is not None:
            self._repository.record_ai_operation(
                actor_target_id=actor_target_id,
                action="create_table",
                table_id=table.id,
                table_title=table.title,
                item_id=None,
                item_title="",
                label_id=item.label.id,
                label_name=item.label.name,
                request_id=request_id,
                changed_fields=("title", "label"),
                before={},
                after=_table_snapshot(item),
            )
        return stored

    def ai_update_table_metadata(
        self,
        *,
        actor_target_id: str,
        request_id: str,
        table_id: int,
        title: str | None = None,
        label_id: int | None = None,
    ) -> dict:
        existing = self._repository.get_operation(request_id, actor_target_id)
        if existing is not None:
            return {**existing, "status": "already_processed", "previous_status": existing.get("status")}
        before_item = self.get_table(table_id, ai_access=True)
        if before_item is None:
            result = _mcp_result("not_found", request_id, table_id=table_id, item_id=None, title="")
            return self._repository.store_operation(
                actor_target_id=actor_target_id,
                operation_type="update_reminder_table",
                result=result,
            )
        updated = self.update_table_metadata(table_id, title=title, label_id=label_id)
        after_item = self.get_table(updated.id)
        if after_item is None:
            result = _mcp_result("failed", request_id, table_id=table_id, item_id=None, title="")
            return self._repository.store_operation(
                actor_target_id=actor_target_id,
                operation_type="update_reminder_table",
                result=result,
            )
        changed = _changed_table_fields(before_item, after_item)
        result = _mcp_result(
            "updated" if changed else "no_change",
            request_id,
            table_id=updated.id,
            item_id=None,
            title=updated.title,
        )
        stored = self._repository.store_operation(
            actor_target_id=actor_target_id,
            operation_type="update_reminder_table",
            result=result,
        )
        if changed:
            self._repository.record_ai_operation(
                actor_target_id=actor_target_id,
                action="move_table" if changed == ("label",) else "update_table",
                table_id=updated.id,
                table_title=updated.title,
                item_id=None,
                item_title="",
                label_id=after_item.label.id,
                label_name=after_item.label.name,
                request_id=request_id,
                changed_fields=changed,
                before=_table_snapshot(before_item),
                after=_table_snapshot(after_item),
            )
        return stored

    def ai_create_item(
        self,
        *,
        actor_target_id: str,
        request_id: str,
        table_id: int,
        draft: ReminderDraft,
    ) -> dict:
        request_fingerprint = _request_fingerprint(
            {
                "table_id": table_id,
                "title": draft.title,
                "lifecycle": draft.lifecycle,
                "action_spec": draft.action_spec,
                "deadline_spec": draft.deadline_spec,
                "note": draft.note,
            }
        )
        existing = self._repository.get_operation(request_id, actor_target_id)
        if existing is not None:
            if existing.get("request_fingerprint") not in (None, request_fingerprint):
                return _mcp_result(
                    "rejected",
                    request_id,
                    table_id=table_id,
                    item_id=existing.get("item_id"),
                    title="",
                    reason="request_id 已用于不同参数；请为新的逻辑请求使用新的 request_id。",
                )
            return {**existing, "status": "already_processed", "previous_status": existing.get("status")}
        table_item = self.get_table(table_id, ai_access=True)
        if table_item is None:
            result = _mcp_result("not_found", request_id, table_id=table_id, item_id=None, title="")
            result["request_fingerprint"] = request_fingerprint
            return self._repository.store_operation(
                actor_target_id=actor_target_id,
                operation_type="create_reminder_item",
                result=result,
            )
        cleaned = self._clean_draft(
            ReminderDraft(
                draft.title,
                draft.lifecycle,
                draft.action_spec,
                draft.deadline_spec,
                draft.note,
                table_id,
            )
        )
        reminder = self.create_reminder(cleaned)
        item = self.get_reminder(reminder.id)
        result = _mcp_result("created", request_id, table_id=table_id, item_id=reminder.id, title=reminder.title)
        result["request_fingerprint"] = request_fingerprint
        stored = self._repository.store_operation(
            actor_target_id=actor_target_id,
            operation_type="create_reminder_item",
            result=result,
        )
        if item is not None:
            self._repository.record_ai_operation(
                actor_target_id=actor_target_id,
                action="create_item",
                table_id=item.table.id,
                table_title=item.table.title,
                item_id=reminder.id,
                item_title=reminder.title,
                label_id=item.label.id,
                label_name=item.label.name,
                request_id=request_id,
                changed_fields=("title", "note", "schedule"),
                before={},
                after=_item_snapshot(item),
            )
        return stored

    def ai_update_item(
        self,
        *,
        actor_target_id: str,
        request_id: str,
        item_id: int,
        title: str | None = None,
        note: str | None = None,
        action_spec: dict | None = None,
        deadline_spec: dict | None = None,
        clear_deadline: bool = False,
        lifecycle: str | None = None,
        multi_notify: bool | None = None,
    ) -> dict:
        existing = self._repository.get_operation(request_id, actor_target_id)
        if existing is not None:
            return {**existing, "status": "already_processed", "previous_status": existing.get("status")}
        before_item = self.get_reminder(item_id, ai_access=True)
        if before_item is None:
            result = _mcp_result("not_found", request_id, table_id=None, item_id=item_id, title="")
            return self._repository.store_operation(
                actor_target_id=actor_target_id,
                operation_type="update_reminder_item",
                result=result,
            )
        before = before_item.reminder
        new_deadline = None if clear_deadline else (
            deadline_spec if deadline_spec is not None else before.deadline_spec
        )
        draft = ReminderDraft(
            title=title if title is not None else before.title,
            lifecycle=lifecycle if lifecycle is not None else before.lifecycle,
            action_spec=action_spec if action_spec is not None else before.action_spec,
            deadline_spec=new_deadline,
            note=note if note is not None else before.note,
            table_id=before.table_id,
            multi_notify=before.multi_notify if multi_notify is None else multi_notify,
            recurrence_mode=before.recurrence_mode,
        )
        updated = self.update_reminder(item_id, draft)
        after_item = self.get_reminder(updated.id)
        if after_item is None:
            result = _mcp_result("failed", request_id, table_id=before.table_id, item_id=item_id, title="")
            return self._repository.store_operation(
                actor_target_id=actor_target_id,
                operation_type="update_reminder_item",
                result=result,
            )
        changed = _changed_fields(before_item, after_item)
        result = _mcp_result(
            "updated" if changed else "no_change",
            request_id,
            table_id=updated.table_id,
            item_id=updated.id,
            title=updated.title,
        )
        stored = self._repository.store_operation(
            actor_target_id=actor_target_id,
            operation_type="update_reminder_item",
            result=result,
        )
        if changed:
            self._repository.record_ai_operation(
                actor_target_id=actor_target_id,
                action="update_item",
                table_id=after_item.table.id,
                table_title=after_item.table.title,
                item_id=updated.id,
                item_title=updated.title,
                label_id=after_item.label.id,
                label_name=after_item.label.name,
                request_id=request_id,
                changed_fields=changed,
                before=_item_snapshot(before_item),
                after=_item_snapshot(after_item),
            )
        return stored

    def ai_complete_item(
        self,
        *,
        actor_target_id: str,
        request_id: str,
        item_id: int,
        current_occurrence_only: bool = True,
    ) -> dict:
        return self._ai_lifecycle_write(
            actor_target_id=actor_target_id,
            request_id=request_id,
            item_id=item_id,
            action="complete",
            operation_type="complete_reminder_item",
            mutate=(
                lambda: self.complete_occurrence(item_id)
                if current_occurrence_only
                else self.complete_reminder(item_id)
            ),
        )

    def ai_pause_item(
        self,
        *,
        actor_target_id: str,
        request_id: str,
        item_id: int,
    ) -> dict:
        return self._ai_lifecycle_write(
            actor_target_id=actor_target_id,
            request_id=request_id,
            item_id=item_id,
            action="pause",
            operation_type="pause_reminder_item",
            mutate=lambda: self.pause_reminder(item_id),
        )

    def ai_resume_item(
        self,
        *,
        actor_target_id: str,
        request_id: str,
        item_id: int,
    ) -> dict:
        return self._ai_lifecycle_write(
            actor_target_id=actor_target_id,
            request_id=request_id,
            item_id=item_id,
            action="resume",
            operation_type="resume_reminder_item",
            mutate=lambda: self.resume_reminder(item_id),
        )

    def _ai_lifecycle_write(
        self,
        *,
        actor_target_id: str,
        request_id: str,
        item_id: int,
        action: str,
        operation_type: str,
        mutate,
    ) -> dict:
        existing = self._repository.get_operation(request_id, actor_target_id)
        if existing is not None:
            return {**existing, "status": "already_processed", "previous_status": existing.get("status")}
        before_item = self.get_reminder(item_id, ai_access=True)
        if before_item is None:
            result = _mcp_result("not_found", request_id, table_id=None, item_id=item_id, title="")
            return self._repository.store_operation(
                actor_target_id=actor_target_id,
                operation_type=operation_type,
                result=result,
            )
        before_snapshot = _item_snapshot(before_item)
        try:
            mutate()
        except ValueError as error:
            if str(error) != "completed_reminder_cannot_resume":
                raise
            result = _mcp_result(
                "rejected",
                request_id,
                table_id=before_item.table.id,
                item_id=item_id,
                title=before_item.reminder.title,
                reason="已完成的提醒不能恢复；完成状态和完成时间已保留。",
            )
            return self._repository.store_operation(
                actor_target_id=actor_target_id,
                operation_type=operation_type,
                result=result,
            )
        after_item = self.get_reminder(item_id)
        result = _mcp_result(
            "updated",
            request_id,
            table_id=before_item.table.id,
            item_id=item_id,
            title=before_item.reminder.title,
        )
        stored = self._repository.store_operation(
            actor_target_id=actor_target_id,
            operation_type=operation_type,
            result=result,
        )
        self._repository.record_ai_operation(
            actor_target_id=actor_target_id,
            action=action,
            table_id=before_item.table.id,
            table_title=before_item.table.title,
            item_id=item_id,
            item_title=before_item.reminder.title,
            label_id=before_item.label.id,
            label_name=before_item.label.name,
            request_id=request_id,
            changed_fields=(action, "occurrence") if action == "complete" else (action,),
            before=before_snapshot,
            after=_item_snapshot(after_item) if after_item is not None else {},
        )
        return stored

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
            title="S-notes Reminder",
            message="Reminder ntfy test OK",
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
        with self._repository.notification_dispatch_lock() as acquired:
            if not acquired:
                return 0
            return self._dispatch_due_notifications(today)

    def _dispatch_due_notifications(self, today: date | None = None) -> int:
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
            if occurrence is None or occurrence.status != OCCURRENCE_PENDING:
                continue
            action_date = date.fromisoformat(occurrence.action_date)
            deadline_date = (
                date.fromisoformat(occurrence.deadline_date)
                if occurrence.deadline_date
                else None
            )
            notification_anchor = deadline_date or action_date
            days_before = (notification_anchor - business_today).days
            if item.reminder.multi_notify and days_before in (7, 3, 1):
                kind = f"advance_{days_before}"
                if not getattr(occurrence, f"{kind}_notified_at"):
                    notifications.append(ReminderNotification(
                        item.reminder.id, occurrence.id, kind, "S-notes 提醒",
                        f"{item.reminder.title} 还有 {days_before} 天到"
                        f"{'最晚处理日' if deadline_date else '开始处理日期'}。",
                    ))
            # Action and deadline share the normal node: never chase an already
            # notified occurrence when it becomes overdue. Retain missed-run recovery.
            if occurrence.action_notified_at or occurrence.deadline_notified_at:
                continue
            if deadline_date is not None and business_today >= deadline_date:
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
            normal_date = notification_anchor if item.reminder.multi_notify else action_date
            if business_today >= normal_date and not occurrence.action_notified_at:
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

    def _list_item(
        self,
        reminder: Reminder,
        table: ReminderTable,
        label: ReminderLabel,
        today: date,
    ) -> ReminderListItem:
        if reminder.lifecycle == REMINDER_PAUSED:
            return ReminderListItem(reminder, table, label, None, "paused", None, None)
        if reminder.lifecycle == REMINDER_COMPLETED:
            return ReminderListItem(reminder, table, label, None, "completed", None, None)
        if reminder.lifecycle == REMINDER_DELETED:
            return ReminderListItem(reminder, table, label, None, "deleted", None, None)

        active = self._active_occurrence(reminder, today)
        if active is None:
            return ReminderListItem(reminder, table, label, None, "completed", None, None)

        if active.status == OCCURRENCE_COMPLETED:
            next_occurrence = self._next_occurrence_after_completion(reminder, active)
            return ReminderListItem(
                reminder,
                table,
                label,
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
        return ReminderListItem(reminder, table, label, active, status, active.action_date, active.deadline_date)

    @staticmethod
    def _matches_query(item: ReminderListItem, filters: ReminderQuery) -> bool:
        text = filters.query.strip().casefold()
        if text:
            haystack = "\n".join(
                (
                    item.reminder.title,
                    item.reminder.note,
                    item.table.title,
                    item.label.name,
                    describe_schedule(item.reminder.action_spec),
                )
            ).casefold()
            if text not in haystack:
                return False
        if filters.date_from or filters.date_to:
            value = item.next_action_date
            if not value:
                return False
            if filters.date_from and value < filters.date_from:
                return False
            if filters.date_to and value > filters.date_to:
                return False
        return True

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
                if next_occurrence is not None and (
                    reminder.multi_notify or reminder.recurrence_mode is not None
                ):
                    return next_occurrence
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
        pending = self._current_uncompleted_occurrence(reminder)
        if pending is not None:
            return pending
        spec = reminder.action_spec
        if reminder.recurrence_mode is not None and spec.get("type") in {
            "interval_days", "interval_from_completion",
        }:
            anchor = date.fromisoformat(occurrence.action_date)
            if reminder.recurrence_mode == "completion_date":
                completed = datetime.fromisoformat(occurrence.completed_at)
                if completed.tzinfo is None:
                    completed = completed.replace(tzinfo=SHANGHAI)
                anchor = completed.astimezone(SHANGHAI).date()
            spec = {**spec, "type": "interval_days", "anchor_date": anchor.isoformat()}
            after = anchor + timedelta(days=1)
        else:
            after = date.fromisoformat(occurrence.action_date)
            if spec.get("type") != "interval_from_completion":
                after += timedelta(days=1)
        action = next_action_date(
            spec,
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
        return True

    def _automatic_table_title(self) -> str:
        stamp = self._now_datetime().strftime("%m-%d %H:%M")
        return f"新提醒表 {stamp}"

    def _table_match(
        self,
        table: ReminderTable,
        label: ReminderLabel,
        reminders: Sequence[ReminderListItem],
        query: str,
    ) -> tuple[int, int | None]:
        text = query.strip().casefold()
        if not text:
            return 0, None
        match_count = 0
        first_item_id: int | None = None
        table_haystack = "\n".join((table.title, label.name)).casefold()
        if text in table_haystack:
            match_count += 1
        for item in reminders:
            item_haystack = "\n".join(
                (
                    item.reminder.title,
                    item.reminder.note,
                    describe_schedule(item.reminder.action_spec),
                )
            ).casefold()
            if text in item_haystack:
                match_count += 1
                if first_item_id is None:
                    first_item_id = item.reminder.id
        return match_count, first_item_id

    def _clean_draft(
        self,
        draft: ReminderDraft,
        *,
        existing: Reminder | None = None,
    ) -> ReminderDraft:
        title = draft.title.strip()
        if not title:
            raise ValueError("reminder_title_required")
        lifecycle = REMINDER_PAUSED if draft.lifecycle == REMINDER_PAUSED else REMINDER_ACTIVE
        table_id = draft.table_id
        if table_id is None:
            table_id = existing.table_id if existing is not None else self.default_table().id
        action_spec, deadline_spec = self._normalize_relative_schedule(
            dict(draft.action_spec),
            dict(draft.deadline_spec) if draft.deadline_spec else None,
        )
        self._validate_schedule(action_spec, deadline_spec)
        if not isinstance(draft.multi_notify, bool):
            raise ValueError("multi_notify_must_be_boolean")
        if draft.recurrence_mode not in (None, "due_date", "completion_date"):
            raise ValueError("unsupported_recurrence_mode")
        if draft.recurrence_mode == "completion_date" and action_spec.get("type") not in {
            "interval_days", "interval_from_completion",
        }:
            raise ValueError("completion_mode_requires_day_interval")
        return ReminderDraft(
            title,
            lifecycle,
            action_spec,
            deadline_spec,
            draft.note.strip(),
            table_id,
            draft.multi_notify,
            draft.recurrence_mode,
        )

    def _validate_schedule(self, action_spec: dict, deadline_spec: dict | None) -> None:
        action_kind = str(action_spec.get("type", ""))
        if action_kind not in {
            "one_time",
            "monthly_day",
            "yearly_date",
            "interval_days",
            "interval_from_completion",
            "nth_weekday",
            "lunar_yearly",
            "cn_a_share_on_or_after",
        }:
            raise ValueError(f"unsupported_reminder_schedule:{action_kind}")
        if not deadline_spec:
            return
        deadline_kind = str(deadline_spec.get("type", ""))
        if deadline_kind not in {
            "same_day",
            "offset_days",
            "one_time",
            "monthly_day",
            "yearly_date",
        }:
            raise ValueError(f"unsupported_reminder_deadline:{deadline_kind}")

    def _normalize_relative_schedule(
        self,
        action_spec: dict,
        deadline_spec: dict | None,
    ) -> tuple[dict, dict | None]:
        action_kind = str(action_spec.get("type", "")).strip().casefold()
        deadline_kind = (
            str(deadline_spec.get("type", "")).strip().casefold()
            if deadline_spec
            else ""
        )
        if action_kind == "relative":
            return self._one_time_after(action_spec), deadline_spec
        return action_spec, deadline_spec

    def _one_time_after(self, spec: dict) -> dict:
        target_date = self._now_datetime() + self._relative_delta(spec)
        return {
            "type": "one_time",
            "date": target_date.date().isoformat(),
        }

    @staticmethod
    def _relative_delta(spec: dict) -> timedelta:
        raw_amount = spec.get("amount", spec.get("value"))
        amount = int(raw_amount)
        if amount <= 0:
            raise ValueError("relative_reminder_amount_must_be_positive")
        unit = str(spec.get("unit", "")).strip().casefold()
        if unit in {"day", "days"}:
            return timedelta(days=amount)
        raise ValueError(f"unsupported_relative_reminder_unit:{unit}")


def _mcp_result(
    status: str,
    request_id: str,
    *,
    table_id: int | None,
    item_id: int | None,
    title: str,
    reason: str = "",
) -> dict:
    return {
        "status": status,
        "request_id": request_id,
        "table_id": table_id,
        "item_id": item_id,
        "reminder_id": item_id,
        "title": title,
        "handling_mode": "direct",
        "modified_formal_reminder": status in {"created", "updated"},
        "entered_confirmation": False,
        "reason": reason,
    }


def _request_fingerprint(payload: dict) -> str:
    encoded = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _is_unsupported_schedule_kind(error: ValueError) -> bool:
    message = str(error)
    return message.startswith(
        (
            "unsupported_reminder_schedule:",
            "unsupported_reminder_deadline:",
        )
    )


def _item_snapshot(item: ReminderListItem | None) -> dict:
    if item is None:
        return {}
    occurrence = item.occurrence
    return {
        "reminder_id": item.reminder.id,
        "table_id": item.table.id,
        "table_title": item.table.title,
        "title": item.reminder.title,
        "note": item.reminder.note,
        "lifecycle": item.reminder.lifecycle,
        "status": item.status,
        "label_id": item.label.id,
        "label": item.label.name,
        "ai_visible": item.table.ai_visible,
        "action_spec": item.reminder.action_spec,
        "deadline_spec": item.reminder.deadline_spec,
        "multi_notify": item.reminder.multi_notify,
        "recurrence_mode": item.reminder.effective_recurrence_mode,
        "next_action_date": item.next_action_date,
        "next_deadline_date": item.next_deadline_date,
        "occurrence": (
            {
                "id": occurrence.id,
                "key": occurrence.occurrence_key,
                "action_date": occurrence.action_date,
                "deadline_date": occurrence.deadline_date,
                "status": occurrence.status,
                "completed_at": occurrence.completed_at,
            }
            if occurrence is not None
            else None
        ),
    }


def _table_snapshot(item: ReminderTableListItem | None) -> dict:
    if item is None:
        return {}
    return {
        "table_id": item.table.id,
        "title": item.table.title,
        "label_id": item.label.id,
        "label": item.label.name,
        "ai_visible": item.table.ai_visible,
        "item_count": item.item_count,
        "updated_at": item.table.updated_at,
    }


def _changed_table_fields(
    before: ReminderTableListItem,
    after: ReminderTableListItem,
) -> tuple[str, ...]:
    fields: list[str] = []
    if before.table.title != after.table.title:
        fields.append("title")
    if before.table.label_id != after.table.label_id:
        fields.append("label")
    return tuple(fields)


def _changed_fields(
    before: ReminderListItem,
    after: ReminderListItem,
) -> tuple[str, ...]:
    fields: list[str] = []
    if before.reminder.multi_notify != after.reminder.multi_notify:
        fields.append("multi_notify")
    if before.reminder.title != after.reminder.title:
        fields.append("title")
    if before.reminder.note != after.reminder.note:
        fields.append("note")
    if before.reminder.lifecycle != after.reminder.lifecycle:
        fields.append("lifecycle")
    if before.reminder.action_spec != after.reminder.action_spec:
        fields.append("action_spec")
    if before.reminder.deadline_spec != after.reminder.deadline_spec:
        fields.append("deadline_spec")
    return tuple(fields)
