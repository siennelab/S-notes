"""SQLite owner for Reminder R1 persistence."""

from __future__ import annotations

import json

from src.snotes.application.ports import ReminderRepository
from src.snotes.domain.reminders import (
    NOTIFICATION_ACTION,
    NOTIFICATION_DEADLINE,
    REMINDER_ACTIVE,
    REMINDER_DELETED,
    Reminder,
    ReminderDraft,
    ReminderOccurrence,
    ReminderSettings,
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
        with self.connections.write() as connection:
            cursor = connection.execute(
                """
                INSERT INTO reminders (
                    title, lifecycle, action_spec_json, deadline_spec_json,
                    note, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
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
        with self.connections.write() as connection:
            cursor = connection.execute(
                """
                UPDATE reminders
                SET title = ?, lifecycle = ?, action_spec_json = ?,
                    deadline_spec_json = ?, note = ?, updated_at = ?
                WHERE id = ? AND lifecycle != ?
                """,
                (
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

    def get(self, reminder_id: int) -> Reminder | None:
        with self.connections.read() as connection:
            row = connection.execute(
                """
                SELECT *
                FROM reminders
                WHERE id = ? AND lifecycle != ?
                """,
                (reminder_id, REMINDER_DELETED),
            ).fetchone()
        return self._reminder_from_row(row) if row is not None else None

    def list_active_records(self) -> list[Reminder]:
        with self.connections.read() as connection:
            rows = connection.execute(
                """
                SELECT *
                FROM reminders
                WHERE lifecycle != ?
                ORDER BY updated_at DESC, id DESC
                """,
                (REMINDER_DELETED,),
            ).fetchall()
        return [self._reminder_from_row(row) for row in rows]

    def set_lifecycle(
        self,
        reminder_id: int,
        lifecycle: str,
        completed_at: str | None = None,
    ) -> None:
        timestamp = timestamp_seconds(self.clock)
        with self.connections.write() as connection:
            connection.execute(
                """
                UPDATE reminders
                SET lifecycle = ?, completed_at = ?, updated_at = ?
                WHERE id = ? AND lifecycle != ?
                """,
                (lifecycle, completed_at, timestamp, reminder_id, REMINDER_DELETED),
            )

    def delete(self, reminder_id: int) -> None:
        with self.connections.write() as connection:
            connection.execute(
                "DELETE FROM reminder_occurrences WHERE reminder_id = ?",
                (reminder_id,),
            )
            connection.execute("DELETE FROM reminders WHERE id = ?", (reminder_id,))

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
            connection.execute(
                """
                UPDATE reminder_occurrences
                SET status = 'completed', completed_at = ?, updated_at = ?
                WHERE id = ?
                """,
                (completed_at, timestamp, occurrence_id),
            )

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
    def _json(value: dict | None) -> str:
        return json.dumps(value, ensure_ascii=True, sort_keys=True) if value else ""
