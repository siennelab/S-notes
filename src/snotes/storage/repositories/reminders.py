"""SQLite owner for Reminder persistence."""

from __future__ import annotations

import json
import uuid
from collections.abc import Sequence

from src.snotes.application.ports import ReminderRepository
from src.snotes.domain.reminders import (
    DEFAULT_REMINDER_LABEL_KEY,
    DEFAULT_REMINDER_LABEL_NAME,
    DEFAULT_REMINDER_TABLE_KEY,
    DEFAULT_REMINDER_TABLE_TITLE,
    NOTIFICATION_ACTION,
    NOTIFICATION_DEADLINE,
    REMINDER_ACTIVE,
    REMINDER_DELETED,
    Reminder,
    ReminderDraft,
    ReminderLabel,
    ReminderOperationLog,
    ReminderOperationPage,
    ReminderOccurrence,
    ReminderSettings,
    ReminderTable,
    ReminderTableListItem,
    ReminderTrashSnapshot,
)
from src.snotes.storage.connection import SQLiteConnectionFactory
from src.snotes.storage.support import Clock, now_shanghai, timestamp_seconds


class SQLiteReminderRepository(ReminderRepository):
    def __init__(
        self,
        connections: SQLiteConnectionFactory,
        *,
        clock: Clock = now_shanghai,
    ) -> None:
        self.connections = connections
        self.clock = clock

    def create(self, draft: ReminderDraft) -> Reminder:
        timestamp = timestamp_seconds(self.clock)
        table_id = draft.table_id or self.default_table().id
        with self.connections.write() as connection:
            self._require_active_table(connection, table_id)
            cursor = connection.execute(
                """
                INSERT INTO reminders (
                    table_id, title, lifecycle, action_spec_json,
                    deadline_spec_json, note, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    table_id,
                    draft.title,
                    draft.lifecycle,
                    self._json(draft.action_spec),
                    self._json(draft.deadline_spec),
                    draft.note,
                    timestamp,
                    timestamp,
                ),
            )
            return self._read_reminder(connection, int(cursor.lastrowid))

    def update(self, reminder_id: int, draft: ReminderDraft) -> Reminder:
        timestamp = timestamp_seconds(self.clock)
        table_id = draft.table_id or self.default_table().id
        with self.connections.write() as connection:
            self._require_active_table(connection, table_id)
            cursor = connection.execute(
                """
                UPDATE reminders
                SET table_id = ?, title = ?, lifecycle = ?, action_spec_json = ?,
                    deadline_spec_json = ?, note = ?, updated_at = ?
                WHERE id = ? AND lifecycle != ?
                """,
                (
                    table_id,
                    draft.title,
                    draft.lifecycle,
                    self._json(draft.action_spec),
                    self._json(draft.deadline_spec),
                    draft.note,
                    timestamp,
                    reminder_id,
                    REMINDER_DELETED,
                ),
            )
            if cursor.rowcount != 1:
                raise ValueError("reminder_not_found")
            return self._read_reminder(connection, reminder_id)

    def get(
        self,
        reminder_id: int,
        *,
        include_deleted: bool = False,
    ) -> Reminder | None:
        with self.connections.read() as connection:
            if include_deleted:
                row = connection.execute(
                    "SELECT * FROM reminders WHERE id = ?",
                    (reminder_id,),
                ).fetchone()
            else:
                row = connection.execute(
                    """
                    SELECT reminders.*
                    FROM reminders
                    JOIN reminder_tables ON reminder_tables.id = reminders.table_id
                    WHERE reminders.id = ?
                      AND reminders.lifecycle != ?
                      AND reminder_tables.is_deleted = 0
                    """,
                    (reminder_id, REMINDER_DELETED),
                ).fetchone()
        return self._reminder_from_row(row) if row is not None else None

    def list_active_records(self, table_id: int | None = None) -> list[Reminder]:
        return self.list_records(include_deleted=False, table_id=table_id)

    def list_records(
        self,
        *,
        include_deleted: bool = False,
        table_id: int | None = None,
    ) -> list[Reminder]:
        parameters: list[object] = []
        clauses: list[str] = []
        if table_id is not None:
            clauses.append("reminders.table_id = ?")
            parameters.append(table_id)
        if not include_deleted:
            clauses.append("reminders.lifecycle != ?")
            parameters.append(REMINDER_DELETED)
            clauses.append("reminder_tables.is_deleted = 0")
        where = "WHERE " + " AND ".join(clauses) if clauses else ""
        with self.connections.read() as connection:
            rows = connection.execute(
                f"""
                SELECT reminders.*
                FROM reminders
                JOIN reminder_tables ON reminder_tables.id = reminders.table_id
                {where}
                ORDER BY reminders.updated_at DESC, reminders.id DESC
                """,
                parameters,
            ).fetchall()
        return [self._reminder_from_row(row) for row in rows]

    def list_labels(self, *, include_deleted: bool = False) -> list[ReminderLabel]:
        with self.connections.read() as connection:
            clause = "" if include_deleted else "WHERE is_deleted = 0"
            rows = connection.execute(
                f"""
                SELECT *
                FROM reminder_labels
                {clause}
                ORDER BY sort_order ASC, lower(name) ASC, id ASC
                """
            ).fetchall()
        return [self._label_from_row(row) for row in rows]

    def get_label(
        self,
        label_id: int,
        *,
        include_deleted: bool = False,
    ) -> ReminderLabel | None:
        with self.connections.read() as connection:
            clause = "" if include_deleted else "AND is_deleted = 0"
            row = connection.execute(
                f"SELECT * FROM reminder_labels WHERE id = ? {clause}",
                (label_id,),
            ).fetchone()
        return self._label_from_row(row) if row is not None else None

    def default_label(self) -> ReminderLabel:
        timestamp = timestamp_seconds(self.clock)
        with self.connections.write() as connection:
            connection.execute(
                """
                INSERT INTO reminder_labels (
                    label_key, name, sort_order, is_deleted, created_at, updated_at
                ) VALUES (?, ?, 0, 0, ?, ?)
                ON CONFLICT(label_key) DO UPDATE SET
                    is_deleted = 0,
                    deleted_at = NULL,
                    updated_at = excluded.updated_at
                """,
                (
                    DEFAULT_REMINDER_LABEL_KEY,
                    DEFAULT_REMINDER_LABEL_NAME,
                    timestamp,
                    timestamp,
                ),
            )
            row = connection.execute(
                "SELECT * FROM reminder_labels WHERE label_key = ?",
                (DEFAULT_REMINDER_LABEL_KEY,),
            ).fetchone()
        if row is None:
            raise RuntimeError("default_reminder_label_missing")
        return self._label_from_row(row)

    def create_label(self, name: str) -> ReminderLabel:
        clean = _clean_label_name(name)
        timestamp = timestamp_seconds(self.clock)
        with self.connections.write() as connection:
            existing = self._active_label_by_name(connection, clean)
            if existing is not None:
                return self._label_from_row(existing)
            sort_row = connection.execute(
                """
                SELECT COALESCE(MAX(sort_order), -1) + 1
                FROM reminder_labels
                WHERE is_deleted = 0
                """
            ).fetchone()
            cursor = connection.execute(
                """
                INSERT INTO reminder_labels (
                    label_key, name, sort_order, is_deleted, created_at, updated_at
                ) VALUES (?, ?, ?, 0, ?, ?)
                """,
                (
                    f"label-{uuid.uuid4().hex}",
                    clean,
                    int(sort_row[0] if sort_row is not None else 0),
                    timestamp,
                    timestamp,
                ),
            )
            return self._read_label(connection, int(cursor.lastrowid), True)

    def rename_label(self, label_id: int, name: str) -> ReminderLabel:
        clean = _clean_label_name(name)
        timestamp = timestamp_seconds(self.clock)
        with self.connections.write() as connection:
            label = self._read_label(connection, label_id, True)
            existing = self._active_label_by_name(connection, clean)
            if existing is not None and int(existing["id"]) != label_id:
                raise ValueError("reminder_label_name_exists")
            connection.execute(
                """
                UPDATE reminder_labels
                SET name = ?, updated_at = ?
                WHERE id = ? AND is_deleted = 0
                """,
                (clean, timestamp, label.id),
            )
            return self._read_label(connection, label.id, True)

    def reorder_labels(self, label_ids: Sequence[int]) -> list[ReminderLabel]:
        timestamp = timestamp_seconds(self.clock)
        ids = [int(value) for value in label_ids]
        with self.connections.write() as connection:
            active_ids = {
                int(row["id"])
                for row in connection.execute(
                    "SELECT id FROM reminder_labels WHERE is_deleted = 0"
                )
            }
            if set(ids) != active_ids:
                raise ValueError("reminder_label_reorder_must_include_all_active_labels")
            for order, label_id in enumerate(ids):
                connection.execute(
                    "UPDATE reminder_labels SET sort_order = ?, updated_at = ? WHERE id = ?",
                    (order, timestamp, label_id),
                )
        return self.list_labels()

    def soft_delete_label_with_tables(self, label_id: int) -> None:
        timestamp = timestamp_seconds(self.clock)
        trash_group_id = f"reminder-label-{label_id}-{uuid.uuid4().hex}"
        with self.connections.write() as connection:
            label = self._read_label(connection, label_id, True)
            if label.is_default:
                raise ValueError("default_reminder_label_cannot_be_deleted")
            connection.execute(
                """
                UPDATE reminder_labels
                SET is_deleted = 1, deleted_at = ?, updated_at = ?
                WHERE id = ? AND is_deleted = 0
                """,
                (timestamp, timestamp, label.id),
            )
            connection.execute(
                """
                UPDATE reminder_tables
                SET is_deleted = 1, deleted_at = ?, trash_group_id = ?,
                    updated_at = ?
                WHERE label_id = ? AND is_deleted = 0
                """,
                (timestamp, trash_group_id, timestamp, label.id),
            )

    def list_tables(self, *, include_deleted: bool = False) -> list[ReminderTable]:
        with self.connections.read() as connection:
            clause = "" if include_deleted else "WHERE is_deleted = 0"
            rows = connection.execute(
                f"""
                SELECT *
                FROM reminder_tables
                {clause}
                ORDER BY sort_order ASC, updated_at DESC, id DESC
                """
            ).fetchall()
        return [self._table_from_row(row) for row in rows]

    def get_table(
        self,
        table_id: int,
        *,
        include_deleted: bool = False,
    ) -> ReminderTable | None:
        with self.connections.read() as connection:
            clause = "" if include_deleted else "AND is_deleted = 0"
            row = connection.execute(
                f"SELECT * FROM reminder_tables WHERE id = ? {clause}",
                (table_id,),
            ).fetchone()
        return self._table_from_row(row) if row is not None else None

    def default_table(self) -> ReminderTable:
        timestamp = timestamp_seconds(self.clock)
        with self.connections.write() as connection:
            label = self._read_label_by_key(connection, DEFAULT_REMINDER_LABEL_KEY)
            connection.execute(
                """
                INSERT INTO reminder_tables (
                    table_key, title, label_id, ai_visible, sort_order,
                    is_deleted, created_at, updated_at
                ) VALUES (?, ?, ?, 1, 0, 0, ?, ?)
                ON CONFLICT(table_key) DO UPDATE SET
                    is_deleted = 0,
                    deleted_at = NULL,
                    trash_group_id = NULL,
                    updated_at = excluded.updated_at
                """,
                (
                    DEFAULT_REMINDER_TABLE_KEY,
                    DEFAULT_REMINDER_TABLE_TITLE,
                    label.id,
                    timestamp,
                    timestamp,
                ),
            )
            row = connection.execute(
                "SELECT * FROM reminder_tables WHERE table_key = ?",
                (DEFAULT_REMINDER_TABLE_KEY,),
            ).fetchone()
        if row is None:
            raise RuntimeError("default_reminder_table_missing")
        return self._table_from_row(row)

    def create_table(
        self,
        *,
        title: str,
        label_id: int | None = None,
        ai_visible: bool = True,
    ) -> ReminderTable:
        clean = _clean_table_title(title)
        timestamp = timestamp_seconds(self.clock)
        selected_label_id = self.default_label().id if label_id is None else int(label_id)
        with self.connections.write() as connection:
            self._require_active_label(connection, selected_label_id)
            sort_row = connection.execute(
                """
                SELECT COALESCE(MAX(sort_order), -1) + 1
                FROM reminder_tables
                WHERE is_deleted = 0
                """
            ).fetchone()
            cursor = connection.execute(
                """
                INSERT INTO reminder_tables (
                    table_key, title, label_id, ai_visible, sort_order,
                    is_deleted, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, 0, ?, ?)
                """,
                (
                    f"table-{uuid.uuid4().hex}",
                    clean,
                    selected_label_id,
                    1 if ai_visible else 0,
                    int(sort_row[0] if sort_row is not None else 0),
                    timestamp,
                    timestamp,
                ),
            )
            return self._read_table(connection, int(cursor.lastrowid), True)

    def update_table_metadata(
        self,
        table_id: int,
        *,
        title: str | None = None,
        label_id: int | None = None,
    ) -> ReminderTable:
        timestamp = timestamp_seconds(self.clock)
        with self.connections.write() as connection:
            table = self._read_table(connection, table_id)
            next_title = _clean_table_title(title) if title is not None else table.title
            next_label_id = int(label_id) if label_id is not None else table.label_id
            self._require_active_label(connection, next_label_id)
            cursor = connection.execute(
                """
                UPDATE reminder_tables
                SET title = ?, label_id = ?, updated_at = ?
                WHERE id = ? AND is_deleted = 0
                """,
                (next_title, next_label_id, timestamp, table_id),
            )
            if cursor.rowcount != 1:
                raise ValueError("reminder_table_not_found")
            return self._read_table(connection, table_id)

    def set_table_ai_visible(self, table_id: int, ai_visible: bool) -> ReminderTable:
        timestamp = timestamp_seconds(self.clock)
        with self.connections.write() as connection:
            cursor = connection.execute(
                """
                UPDATE reminder_tables
                SET ai_visible = ?, updated_at = ?
                WHERE id = ? AND is_deleted = 0
                """,
                (1 if ai_visible else 0, timestamp, table_id),
            )
            if cursor.rowcount != 1:
                raise ValueError("reminder_table_not_found")
            return self._read_table(connection, table_id)

    def soft_delete_table(self, table_id: int) -> None:
        timestamp = timestamp_seconds(self.clock)
        with self.connections.write() as connection:
            cursor = connection.execute(
                """
                UPDATE reminder_tables
                SET is_deleted = 1, deleted_at = ?, trash_group_id = ?,
                    updated_at = ?
                WHERE id = ? AND is_deleted = 0
                """,
                (
                    timestamp,
                    f"reminder-table-{table_id}-{uuid.uuid4().hex}",
                    timestamp,
                    table_id,
                ),
            )
            if cursor.rowcount != 1:
                raise ValueError("reminder_table_not_found")

    def restore_table(self, table_id: int) -> ReminderTable:
        timestamp = timestamp_seconds(self.clock)
        with self.connections.write() as connection:
            table = self._read_table(connection, table_id, include_deleted=True)
            label = self._read_label(connection, table.label_id, include_deleted=True)
            if label.is_deleted:
                connection.execute(
                    """
                    UPDATE reminder_labels
                    SET is_deleted = 0, deleted_at = NULL, updated_at = ?
                    WHERE id = ?
                    """,
                    (timestamp, label.id),
                )
            cursor = connection.execute(
                """
                UPDATE reminder_tables
                SET is_deleted = 0, deleted_at = NULL, trash_group_id = NULL,
                    updated_at = ?
                WHERE id = ?
                """,
                (timestamp, table_id),
            )
            if cursor.rowcount != 1:
                raise ValueError("reminder_table_not_found")
            return self._read_table(connection, table_id)

    def permanently_delete_table(self, table_id: int) -> None:
        with self.connections.write() as connection:
            table = connection.execute(
                "SELECT id FROM reminder_tables WHERE id = ? AND is_deleted = 1",
                (table_id,),
            ).fetchone()
            if table is None:
                raise ValueError("reminder_table_not_found")
            ids = [
                int(row["id"])
                for row in connection.execute(
                    "SELECT id FROM reminders WHERE table_id = ?",
                    (table_id,),
                ).fetchall()
            ]
            if ids:
                placeholders = ",".join("?" for _ in ids)
                connection.execute(
                    f"DELETE FROM reminder_occurrences WHERE reminder_id IN ({placeholders})",
                    ids,
                )
                connection.execute(
                    f"DELETE FROM reminders WHERE id IN ({placeholders})",
                    ids,
                )
            connection.execute("DELETE FROM reminder_tables WHERE id = ?", (table_id,))

    def count_items_by_table(self, table_id: int, *, include_deleted: bool = False) -> int:
        clause = "" if include_deleted else "AND lifecycle != ?"
        parameters: list[object] = [table_id]
        if not include_deleted:
            parameters.append(REMINDER_DELETED)
        with self.connections.read() as connection:
            row = connection.execute(
                f"SELECT COUNT(*) FROM reminders WHERE table_id = ? {clause}",
                parameters,
            ).fetchone()
        return int(row[0]) if row is not None else 0

    def set_lifecycle(
        self,
        reminder_id: int,
        lifecycle: str,
        completed_at: str | None = None,
    ) -> None:
        timestamp = timestamp_seconds(self.clock)
        with self.connections.write() as connection:
            cursor = connection.execute(
                """
                UPDATE reminders
                SET lifecycle = ?, completed_at = ?, updated_at = ?
                WHERE id = ? AND lifecycle != ?
                """,
                (lifecycle, completed_at, timestamp, reminder_id, REMINDER_DELETED),
            )
            if cursor.rowcount != 1:
                raise ValueError("reminder_not_found")

    def list_trash(self) -> ReminderTrashSnapshot:
        with self.connections.read() as connection:
            table_rows = connection.execute(
                """
                SELECT reminder_tables.*,
                       reminder_labels.id AS label_row_id,
                       reminder_labels.label_key AS label_key,
                       reminder_labels.name AS label_name,
                       reminder_labels.sort_order AS label_sort_order,
                       reminder_labels.is_deleted AS label_is_deleted,
                       reminder_labels.created_at AS label_created_at,
                       reminder_labels.updated_at AS label_updated_at,
                       reminder_labels.deleted_at AS label_deleted_at,
                       COUNT(reminders.id) AS item_count
                FROM reminder_tables
                JOIN reminder_labels ON reminder_labels.id = reminder_tables.label_id
                LEFT JOIN reminders ON reminders.table_id = reminder_tables.id
                WHERE reminder_tables.is_deleted = 1
                GROUP BY reminder_tables.id
                ORDER BY reminder_tables.deleted_at DESC, reminder_tables.id DESC
                """
            ).fetchall()
        tables = tuple(
            ReminderTableListItem(
                self._table_from_row(row),
                ReminderLabel(
                    id=int(row["label_row_id"]),
                    label_key=str(row["label_key"]),
                    name=str(row["label_name"]),
                    sort_order=int(row["label_sort_order"]),
                    is_deleted=bool(row["label_is_deleted"]),
                    created_at=str(row["label_created_at"]),
                    updated_at=str(row["label_updated_at"]),
                    deleted_at=row["label_deleted_at"],
                ),
                int(row["item_count"]),
            )
            for row in table_rows
        )
        label_ids = sorted({item.table.label_id for item in tables})
        labels = tuple(
            label
            for label_id in label_ids
            if (label := self.get_label(label_id, include_deleted=True)) is not None
        )
        return ReminderTrashSnapshot(labels, tables)

    def delete(self, reminder_id: int) -> None:
        with self.connections.write() as connection:
            connection.execute(
                "DELETE FROM reminder_occurrences WHERE reminder_id = ?",
                (reminder_id,),
            )
            cursor = connection.execute("DELETE FROM reminders WHERE id = ?", (reminder_id,))
            if cursor.rowcount != 1:
                raise ValueError("reminder_not_found")

    def permanently_delete_trashed(self, reminder_id: int) -> None:
        self.delete(reminder_id)

    def upsert_occurrence(
        self,
        reminder_id: int,
        occurrence_key: str,
        action_date: str,
        deadline_date: str | None,
    ) -> ReminderOccurrence:
        timestamp = timestamp_seconds(self.clock)
        with self.connections.write() as connection:
            connection.execute(
                """
                INSERT INTO reminder_occurrences (
                    reminder_id, occurrence_key, action_date, deadline_date,
                    status, created_at, updated_at
                ) VALUES (?, ?, ?, ?, 'pending', ?, ?)
                ON CONFLICT(reminder_id, occurrence_key) DO UPDATE SET
                    action_date = excluded.action_date,
                    deadline_date = excluded.deadline_date,
                    updated_at = excluded.updated_at
                """,
                (
                    reminder_id,
                    occurrence_key,
                    action_date,
                    deadline_date,
                    timestamp,
                    timestamp,
                ),
            )
            return self._read_occurrence(connection, reminder_id, occurrence_key)

    def get_occurrence(
        self,
        reminder_id: int,
        occurrence_key: str,
    ) -> ReminderOccurrence | None:
        with self.connections.read() as connection:
            row = connection.execute(
                """
                SELECT *
                FROM reminder_occurrences
                WHERE reminder_id = ? AND occurrence_key = ?
                """,
                (reminder_id, occurrence_key),
            ).fetchone()
        return self._occurrence_from_row(row) if row is not None else None

    def list_occurrences(self, reminder_id: int) -> list[ReminderOccurrence]:
        with self.connections.read() as connection:
            rows = connection.execute(
                """
                SELECT *
                FROM reminder_occurrences
                WHERE reminder_id = ?
                ORDER BY action_date ASC, id ASC
                """,
                (reminder_id,),
            ).fetchall()
        return [self._occurrence_from_row(row) for row in rows]

    def delete_pending_occurrences(self, reminder_id: int) -> None:
        with self.connections.write() as connection:
            connection.execute(
                """
                DELETE FROM reminder_occurrences
                WHERE reminder_id = ? AND status = 'pending'
                """,
                (reminder_id,),
            )

    def mark_occurrence_completed(self, occurrence_id: int, completed_at: str) -> None:
        timestamp = timestamp_seconds(self.clock)
        with self.connections.write() as connection:
            cursor = connection.execute(
                """
                UPDATE reminder_occurrences
                SET status = 'completed', completed_at = ?, updated_at = ?
                WHERE id = ?
                """,
                (completed_at, timestamp, occurrence_id),
            )
            if cursor.rowcount != 1:
                raise ValueError("reminder_occurrence_not_found")

    def mark_notified(self, occurrence_id: int, kind: str, notified_at: str) -> None:
        column = {
            NOTIFICATION_ACTION: "action_notified_at",
            NOTIFICATION_DEADLINE: "deadline_notified_at",
        }[kind]
        timestamp = timestamp_seconds(self.clock)
        with self.connections.write() as connection:
            connection.execute(
                f"""
                UPDATE reminder_occurrences
                SET {column} = ?, updated_at = ?
                WHERE id = ?
                """,
                (notified_at, timestamp, occurrence_id),
            )

    def settings(self) -> ReminderSettings:
        return ReminderSettings(
            notifications_enabled=self._bool("reminder/notifications_enabled", True),
            daily_time=self.get_text_setting("reminder/daily_time", "10:00"),
            ntfy_endpoint=self.get_text_setting("reminder/ntfy_endpoint", ""),
            ntfy_topic=self.get_text_setting("reminder/ntfy_topic", ""),
        )

    def save_settings(self, settings: ReminderSettings) -> None:
        self.set_text_setting("reminder/notifications_enabled", "1" if settings.notifications_enabled else "0")
        self.set_text_setting("reminder/daily_time", settings.daily_time or "10:00")
        self.set_text_setting("reminder/ntfy_endpoint", settings.ntfy_endpoint or "")
        self.set_text_setting("reminder/ntfy_topic", settings.ntfy_topic)

    def get_text_setting(self, key: str, default: str = "") -> str:
        with self.connections.read() as connection:
            row = connection.execute(
                "SELECT setting_value FROM app_settings WHERE setting_key = ?",
                (key,),
            ).fetchone()
        return str(row["setting_value"]) if row is not None else default

    def set_text_setting(self, key: str, value: str) -> None:
        timestamp = timestamp_seconds(self.clock)
        with self.connections.write() as connection:
            connection.execute(
                """
                INSERT INTO app_settings (setting_key, setting_value, updated_at)
                VALUES (?, ?, ?)
                ON CONFLICT(setting_key) DO UPDATE SET
                    setting_value = excluded.setting_value,
                    updated_at = excluded.updated_at
                """,
                (key, str(value), timestamp),
            )

    def get_operation(
        self,
        request_id: str,
        actor_target_id: str | None = None,
    ) -> dict | None:
        parameters: list[object] = [request_id]
        actor_clause = ""
        if actor_target_id is not None:
            actor_clause = " AND actor_target_id = ?"
            parameters.append(actor_target_id)
        with self.connections.read() as connection:
            row = connection.execute(
                """
                SELECT operation_type, result_json, created_at, updated_at
                FROM ai_operations WHERE request_id = ?
                """
                + actor_clause,
                parameters,
            ).fetchone()
        if row is None:
            return None
        return {
            **json.loads(str(row["result_json"])),
            "operation_type": str(row["operation_type"]),
            "created_at": str(row["created_at"]),
            "updated_at": str(row["updated_at"]),
        }

    def store_operation(
        self,
        *,
        actor_target_id: str,
        operation_type: str,
        result: dict,
    ) -> dict:
        timestamp = timestamp_seconds(self.clock)
        with self.connections.write() as connection:
            existing = connection.execute(
                """
                SELECT operation_type, result_json, created_at, updated_at
                FROM ai_operations WHERE request_id = ?
                """,
                (result["request_id"],),
            ).fetchone()
            if existing is not None:
                existing_result = json.loads(str(existing["result_json"]))
                return {
                    **existing_result,
                    "status": "already_processed",
                    "previous_status": existing_result.get("status"),
                    "operation_type": str(existing["operation_type"]),
                    "created_at": str(existing["created_at"]),
                    "updated_at": str(existing["updated_at"]),
                }
            connection.execute(
                """
                INSERT INTO ai_operations (
                    request_id, actor_target_id, operation_type, status,
                    result_json, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    result["request_id"],
                    actor_target_id,
                    operation_type,
                    result["status"],
                    json.dumps(result, ensure_ascii=False),
                    timestamp,
                    timestamp,
                ),
            )
        return result

    def record_ai_operation(
        self,
        *,
        actor_target_id: str,
        action: str,
        table_id: int | None,
        table_title: str,
        item_id: int | None,
        item_title: str,
        label_id: int | None,
        label_name: str,
        request_id: str,
        changed_fields: Sequence[str],
        before: dict,
        after: dict,
    ) -> None:
        timestamp = timestamp_seconds(self.clock)
        with self.connections.write() as connection:
            connection.execute(
                """
                INSERT INTO ai_reminder_operations (
                    happened_at, actor_target_id, action, table_id, table_title,
                    item_id, item_title, label_id, label_name, request_id,
                    changed_fields_json, before_json, after_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    timestamp,
                    actor_target_id,
                    action,
                    table_id,
                    table_title,
                    item_id,
                    item_title,
                    label_id,
                    label_name,
                    request_id,
                    json.dumps(list(changed_fields), ensure_ascii=False),
                    json.dumps(before, ensure_ascii=False, sort_keys=True),
                    json.dumps(after, ensure_ascii=False, sort_keys=True),
                ),
            )

    def list_ai_operation_logs(
        self,
        *,
        offset: int = 0,
        limit: int = 50,
    ) -> ReminderOperationPage:
        safe_offset = max(0, int(offset))
        safe_limit = max(1, min(200, int(limit)))
        with self.connections.read() as connection:
            total = int(
                connection.execute(
                    "SELECT COUNT(*) FROM ai_reminder_operations"
                ).fetchone()[0]
            )
            rows = connection.execute(
                """
                SELECT *
                FROM ai_reminder_operations
                ORDER BY id DESC
                LIMIT ? OFFSET ?
                """,
                (safe_limit, safe_offset),
            ).fetchall()
        logs = tuple(self._operation_log_from_row(row) for row in rows)
        return ReminderOperationPage(logs, total, safe_offset, safe_limit)

    def _bool(self, key: str, default: bool) -> bool:
        raw = self.get_text_setting(key, "1" if default else "0")
        return raw in {"1", "true", "True", "yes", "on"}

    def _read_reminder(self, connection, reminder_id: int) -> Reminder:
        row = connection.execute(
            "SELECT * FROM reminders WHERE id = ?",
            (reminder_id,),
        ).fetchone()
        if row is None:
            raise ValueError("reminder_not_found")
        return self._reminder_from_row(row)

    def _read_label(
        self,
        connection,
        label_id: int,
        include_deleted: bool = False,
    ) -> ReminderLabel:
        clause = "" if include_deleted else "AND is_deleted = 0"
        row = connection.execute(
            f"SELECT * FROM reminder_labels WHERE id = ? {clause}",
            (label_id,),
        ).fetchone()
        if row is None:
            raise ValueError("reminder_label_not_found")
        return self._label_from_row(row)

    def _read_label_by_key(self, connection, label_key: str) -> ReminderLabel:
        row = connection.execute(
            "SELECT * FROM reminder_labels WHERE label_key = ?",
            (label_key,),
        ).fetchone()
        if row is None:
            raise ValueError("reminder_label_not_found")
        return self._label_from_row(row)

    def _require_active_label(self, connection, label_id: int) -> None:
        self._read_label(connection, label_id, include_deleted=False)

    def _read_table(
        self,
        connection,
        table_id: int,
        include_deleted: bool = False,
    ) -> ReminderTable:
        clause = "" if include_deleted else "AND is_deleted = 0"
        row = connection.execute(
            f"SELECT * FROM reminder_tables WHERE id = ? {clause}",
            (table_id,),
        ).fetchone()
        if row is None:
            raise ValueError("reminder_table_not_found")
        return self._table_from_row(row)

    def _require_active_table(self, connection, table_id: int) -> None:
        table = self._read_table(connection, table_id, include_deleted=False)
        self._read_label(connection, table.label_id, include_deleted=False)

    @staticmethod
    def _active_label_by_name(connection, name: str):
        return connection.execute(
            """
            SELECT *
            FROM reminder_labels
            WHERE is_deleted = 0 AND lower(trim(name)) = lower(trim(?))
            """,
            (name,),
        ).fetchone()

    def _read_occurrence(
        self,
        connection,
        reminder_id: int,
        key: str,
    ) -> ReminderOccurrence:
        row = connection.execute(
            """
            SELECT *
            FROM reminder_occurrences
            WHERE reminder_id = ? AND occurrence_key = ?
            """,
            (reminder_id, key),
        ).fetchone()
        if row is None:
            raise ValueError("reminder_occurrence_not_found")
        return self._occurrence_from_row(row)

    @staticmethod
    def _reminder_from_row(row) -> Reminder:
        return Reminder(
            id=int(row["id"]),
            table_id=int(row["table_id"]),
            title=str(row["title"]),
            lifecycle=str(row["lifecycle"] or REMINDER_ACTIVE),
            action_spec=json.loads(str(row["action_spec_json"])),
            deadline_spec=(
                json.loads(str(row["deadline_spec_json"]))
                if row["deadline_spec_json"]
                else None
            ),
            note=str(row["note"] or ""),
            created_at=str(row["created_at"]),
            updated_at=str(row["updated_at"]),
            completed_at=row["completed_at"],
        )

    @staticmethod
    def _label_from_row(row) -> ReminderLabel:
        return ReminderLabel(
            id=int(row["id"]),
            label_key=str(row["label_key"]),
            name=str(row["name"]),
            sort_order=int(row["sort_order"]),
            is_deleted=bool(row["is_deleted"]),
            created_at=str(row["created_at"]),
            updated_at=str(row["updated_at"]),
            deleted_at=row["deleted_at"],
        )

    @staticmethod
    def _table_from_row(row) -> ReminderTable:
        return ReminderTable(
            id=int(row["id"]),
            table_key=str(row["table_key"]),
            title=str(row["title"]),
            label_id=int(row["label_id"]),
            ai_visible=bool(row["ai_visible"]),
            sort_order=int(row["sort_order"]),
            is_deleted=bool(row["is_deleted"]),
            created_at=str(row["created_at"]),
            updated_at=str(row["updated_at"]),
            deleted_at=row["deleted_at"],
            trash_group_id=row["trash_group_id"],
        )

    @staticmethod
    def _occurrence_from_row(row) -> ReminderOccurrence:
        return ReminderOccurrence(
            id=int(row["id"]),
            reminder_id=int(row["reminder_id"]),
            occurrence_key=str(row["occurrence_key"]),
            action_date=str(row["action_date"]),
            deadline_date=row["deadline_date"],
            status=str(row["status"]),
            completed_at=row["completed_at"],
            action_notified_at=row["action_notified_at"],
            deadline_notified_at=row["deadline_notified_at"],
            created_at=str(row["created_at"]),
            updated_at=str(row["updated_at"]),
        )

    @staticmethod
    def _operation_log_from_row(row) -> ReminderOperationLog:
        return ReminderOperationLog(
            id=int(row["id"]),
            happened_at=str(row["happened_at"]),
            actor_target_id=str(row["actor_target_id"]),
            action=str(row["action"]),
            table_id=int(row["table_id"]) if row["table_id"] is not None else None,
            table_title=str(row["table_title"] or ""),
            item_id=int(row["item_id"]) if row["item_id"] is not None else None,
            item_title=str(row["item_title"] or ""),
            label_id=int(row["label_id"]) if row["label_id"] is not None else None,
            label_name=str(row["label_name"] or ""),
            request_id=str(row["request_id"]),
            changed_fields=tuple(json.loads(str(row["changed_fields_json"] or "[]"))),
            before_json=str(row["before_json"] or "{}"),
            after_json=str(row["after_json"] or "{}"),
        )

    @staticmethod
    def _json(value: dict | None) -> str:
        return json.dumps(value, ensure_ascii=True, sort_keys=True) if value else ""


def _clean_label_name(value: str) -> str:
    clean = str(value or "").strip()
    if not clean:
        raise ValueError("reminder_label_name_required")
    if len(clean) > 80:
        raise ValueError("reminder_label_name_too_long")
    return clean


def _clean_table_title(value: str) -> str:
    clean = str(value or "").strip()
    if not clean:
        raise ValueError("reminder_table_title_required")
    if len(clean) > 200:
        raise ValueError("reminder_table_title_too_long")
    return clean
