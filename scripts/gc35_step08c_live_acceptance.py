"""Scoped Step 8C production multi-notification acceptance runner.

This script deliberately does not create, activate, or delete fixtures.  Those
operations remain owner-HTTP actions performed by the Step 8C orchestrator.
It runs the real production scheduler and authenticated transport, but replaces
the repository at the composition point with a fail-closed view containing one
pre-validated fictional fixture.

The private JSON state file has this shape::

    {
      "schema": 1,
      "purpose": "gc35-step08c-live-acceptance",
      "database": "/srv/snotes/data/reminder-production.db",
      "topic": "s-reminder-prod",
      "fixtures": [
        {
          "reminder_id": 123,
          "occurrence_id": 456,
          "title": "FICTIONAL Step08C multi deadline",
          "action_date": "2026-10-17",
          "deadline_date": "2026-10-24"
        },
        {
          "reminder_id": 124,
          "occurrence_id": 457,
          "title": "FICTIONAL Step08C multi start",
          "action_date": "2026-10-24",
          "deadline_date": null
        }
      ]
    }

Only the two fixed titles above are accepted.  Both fixtures must have the note
``Temporary controlled-schedule fixture; existing Reminder rows must never be
touched``.  The caller selects exactly one manifest entry with ``--fixture-id``.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sqlite3
import stat
from contextlib import closing, contextmanager
from dataclasses import dataclass
from datetime import date, datetime, time
from pathlib import Path
from typing import Iterator, Sequence

from src import reminder_cloud
from src.snotes.domain.reminder_schedule import SHANGHAI
from src.snotes.domain.reminders import ReminderOccurrence, ReminderSettings


STATE_PURPOSE = "gc35-step08c-live-acceptance"
STATE_SCHEMA = 1
DEADLINE_TITLE = "FICTIONAL Step08C multi deadline"
START_TITLE = "FICTIONAL Step08C multi start"
FIXTURE_NOTE = (
    "Temporary controlled-schedule fixture; existing Reminder rows must never be touched"
)
ALLOWED_ADVANCE_DAYS = (7, 3, 1, 0)
MARK_COLUMNS = {
    "advance_7": "advance_7_notified_at",
    "advance_3": "advance_3_notified_at",
    "advance_1": "advance_1_notified_at",
    "action": "action_notified_at",
    "deadline": "deadline_notified_at",
}
LAST_SCHEDULED_DATE_KEY = "reminder/last_scheduled_date"


@dataclass(frozen=True)
class FixtureState:
    database: Path
    topic: str
    reminder_id: int
    occurrence_id: int
    title: str
    action_date: date
    deadline_date: date | None

    @property
    def anchor_date(self) -> date:
        return self.deadline_date or self.action_date


def _private_state_file(path: Path) -> None:
    metadata = path.lstat()
    mode = stat.S_IMODE(metadata.st_mode)
    if not stat.S_ISREG(metadata.st_mode) or stat.S_ISLNK(metadata.st_mode):
        raise RuntimeError("live_acceptance_state_regular_file_required")
    owned_private = metadata.st_uid == os.geteuid() and mode in {0o400, 0o600}
    systemd_credential = (
        metadata.st_uid == 0
        and mode == 0o440
        and path.parent.parent == Path("/run/credentials")
        and not path.parent.is_symlink()
    )
    if not (owned_private or systemd_credential):
        raise RuntimeError("live_acceptance_state_permissions_invalid")


def load_fixture_state(path: Path, fixture_id: int) -> FixtureState:
    _private_state_file(path)
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as error:
        raise RuntimeError("live_acceptance_state_invalid") from error
    required = {
        "schema",
        "purpose",
        "database",
        "topic",
        "fixtures",
    }
    if not isinstance(payload, dict) or set(payload) != required:
        raise RuntimeError("live_acceptance_state_invalid")
    if (
        payload["schema"] != STATE_SCHEMA
        or payload["purpose"] != STATE_PURPOSE
        or not isinstance(payload["fixtures"], list)
        or len(payload["fixtures"]) != 2
    ):
        raise RuntimeError("live_acceptance_state_invalid")
    matches = [
        fixture
        for fixture in payload["fixtures"]
        if isinstance(fixture, dict) and fixture.get("reminder_id") == fixture_id
    ]
    if len(matches) != 1:
        raise RuntimeError("live_acceptance_fixture_not_in_manifest")
    fixture = matches[0]
    fixture_keys = {
        "reminder_id",
        "occurrence_id",
        "title",
        "action_date",
        "deadline_date",
    }
    if set(fixture) != fixture_keys:
        raise RuntimeError("live_acceptance_state_invalid")
    try:
        database = Path(payload["database"])
        reminder_id = int(fixture["reminder_id"])
        occurrence_id = int(fixture["occurrence_id"])
        action_date = date.fromisoformat(fixture["action_date"])
        deadline_date = (
            date.fromisoformat(fixture["deadline_date"])
            if fixture["deadline_date"] is not None
            else None
        )
    except (TypeError, ValueError) as error:
        raise RuntimeError("live_acceptance_state_invalid") from error
    if (
        not database.is_absolute()
        or reminder_id <= 0
        or occurrence_id <= 0
        or not isinstance(payload["topic"], str)
        or fixture["title"] not in {DEADLINE_TITLE, START_TITLE}
        or (fixture["title"] == DEADLINE_TITLE) != (deadline_date is not None)
    ):
        raise RuntimeError("live_acceptance_state_invalid")
    return FixtureState(
        database=database,
        topic=payload["topic"],
        reminder_id=reminder_id,
        occurrence_id=occurrence_id,
        title=fixture["title"],
        action_date=action_date,
        deadline_date=deadline_date,
    )


def _expected_kind(state: FixtureState, business_date: date) -> str:
    days_before = (state.anchor_date - business_date).days
    if days_before not in ALLOWED_ADVANCE_DAYS:
        raise RuntimeError("live_acceptance_business_date_not_a_test_node")
    if days_before:
        return f"advance_{days_before}"
    return "deadline" if state.deadline_date is not None else "action"


def _read_fixture(database: Path, state: FixtureState) -> tuple[dict, list[dict]]:
    uri = f"{database.resolve().as_uri()}?mode=ro"
    with closing(sqlite3.connect(uri, uri=True, timeout=5.0)) as connection:
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA busy_timeout = 5000")
        reminder = connection.execute(
            "SELECT * FROM reminders WHERE id = ?",
            (state.reminder_id,),
        ).fetchone()
        occurrences = connection.execute(
            "SELECT * FROM reminder_occurrences WHERE reminder_id = ? ORDER BY id",
            (state.reminder_id,),
        ).fetchall()
    if reminder is None:
        raise RuntimeError("live_acceptance_fixture_missing")
    return dict(reminder), [dict(row) for row in occurrences]


def _validate_fixture(database: Path, state: FixtureState) -> None:
    reminder, occurrences = _read_fixture(database, state)
    try:
        action_spec = json.loads(reminder["action_spec_json"])
        deadline_spec = (
            json.loads(reminder["deadline_spec_json"])
            if reminder["deadline_spec_json"]
            else None
        )
    except (TypeError, json.JSONDecodeError) as error:
        raise RuntimeError("live_acceptance_fixture_invalid") from error
    expected_deadline = (
        {"type": "one_time", "date": state.deadline_date.isoformat()}
        if state.deadline_date is not None
        else None
    )
    expected = {
        "title": state.title,
        "note": FIXTURE_NOTE,
        "lifecycle": "active",
        "multi_notify": 1,
    }
    if (
        any(reminder.get(key) != value for key, value in expected.items())
        or action_spec != {"type": "one_time", "date": state.action_date.isoformat()}
        or deadline_spec != expected_deadline
        or len(occurrences) != 1
        or int(occurrences[0]["id"]) != state.occurrence_id
        or occurrences[0]["status"] != "pending"
        or occurrences[0]["occurrence_key"] != state.action_date.isoformat()
        or occurrences[0]["action_date"] != state.action_date.isoformat()
        or occurrences[0]["deadline_date"]
        != (state.deadline_date.isoformat() if state.deadline_date else None)
    ):
        raise RuntimeError("live_acceptance_fixture_invalid")


def _safe_value(value: object) -> object:
    if isinstance(value, bytes):
        return {"blob_sha256": hashlib.sha256(value).hexdigest(), "length": len(value)}
    if value is None or isinstance(value, (str, int, float)):
        return value
    return str(value)


def _quote_identifier(value: str) -> str:
    return '"' + value.replace('"', '""') + '"'


def _unscoped_table_digests(database: Path, reminder_id: int) -> dict[str, str]:
    """Hash every table after excluding only explicitly allowed scheduler rows."""
    uri = f"{database.resolve().as_uri()}?mode=ro"
    with closing(sqlite3.connect(uri, uri=True, timeout=5.0)) as connection:
        connection.execute("PRAGMA busy_timeout = 5000")
        tables = [
            str(row[0])
            for row in connection.execute(
                """
                SELECT name FROM sqlite_schema
                WHERE type = 'table' AND name NOT LIKE 'sqlite_%'
                ORDER BY name
                """
            )
        ]
        result: dict[str, str] = {}
        for table in tables:
            quoted = _quote_identifier(table)
            columns = [
                str(row[1])
                for row in connection.execute(f"PRAGMA table_info({quoted})")
            ]
            if not columns:
                continue
            where = ""
            parameters: tuple[object, ...] = ()
            if table == "reminder_occurrences":
                where = " WHERE reminder_id != ?"
                parameters = (reminder_id,)
            rows = connection.execute(
                f"SELECT * FROM {quoted}{where}",
                parameters,
            ).fetchall()
            canonical = sorted(
                json.dumps(
                    [_safe_value(value) for value in row],
                    ensure_ascii=False,
                    separators=(",", ":"),
                    sort_keys=True,
                )
                for row in rows
            )
            digest = hashlib.sha256()
            digest.update(json.dumps(columns, separators=(",", ":")).encode())
            for row in canonical:
                digest.update(b"\n")
                digest.update(row.encode("utf-8"))
            result[table] = digest.hexdigest()
    return result


class _ScopedFixtureRepository:
    """Expose only one fixture and reject every unneeded write method."""

    def __init__(
        self,
        repository,
        state: FixtureState,
        business_date: date,
        expected_kind: str,
    ) -> None:
        self._repository = repository
        self._state = state
        self._business_date = business_date
        self._expected_kind = expected_kind

    def list_labels(self, *, include_deleted: bool = False):
        return self._repository.list_labels(include_deleted=include_deleted)

    def list_tables(self, *, include_deleted: bool = False):
        return self._repository.list_tables(include_deleted=include_deleted)

    def list_records(self, *, include_deleted: bool = False, table_id=None):
        if include_deleted or table_id is not None:
            raise RuntimeError("live_acceptance_unscoped_list_rejected")
        reminder = self._repository.get(self._state.reminder_id)
        if reminder is None:
            raise RuntimeError("live_acceptance_fixture_missing")
        return [reminder]

    def list_occurrences(self, reminder_id: int) -> list[ReminderOccurrence]:
        self._require_reminder(reminder_id)
        return self._repository.list_occurrences(reminder_id)

    def upsert_occurrence(
        self,
        reminder_id: int,
        occurrence_key: str,
        action_date: str,
        deadline_date: str | None,
    ) -> ReminderOccurrence:
        self._require_reminder(reminder_id)
        if (
            occurrence_key != self._state.action_date.isoformat()
            or action_date != self._state.action_date.isoformat()
            or deadline_date
            != (self._state.deadline_date.isoformat() if self._state.deadline_date else None)
        ):
            raise RuntimeError("live_acceptance_occurrence_write_rejected")
        occurrence = self._repository.upsert_occurrence(
            reminder_id,
            occurrence_key,
            action_date,
            deadline_date,
        )
        self._require_occurrence(occurrence.id)
        return occurrence

    def notification_dispatch_lock(self):
        return self._repository.notification_dispatch_lock()

    def mark_notified(self, occurrence_id: int, kind: str, notified_at: str) -> None:
        self._require_occurrence(occurrence_id)
        if kind != self._expected_kind:
            raise RuntimeError("live_acceptance_notification_kind_rejected")
        self._repository.mark_notified(occurrence_id, kind, notified_at)

    def settings(self) -> ReminderSettings:
        return self._repository.settings()

    def save_settings(self, settings: ReminderSettings) -> None:
        raise RuntimeError("live_acceptance_settings_write_rejected")

    def set_text_setting(self, key: str, value: str) -> None:
        if key != LAST_SCHEDULED_DATE_KEY or value != self._business_date.isoformat():
            raise RuntimeError("live_acceptance_setting_write_rejected")
        # A controlled future date belongs only to this fixture. Do not move
        # the production scheduler's global last-run metadata into the future.

    def default_label(self):
        raise RuntimeError("live_acceptance_default_label_write_rejected")

    def _require_reminder(self, reminder_id: int) -> None:
        if int(reminder_id) != self._state.reminder_id:
            raise RuntimeError("live_acceptance_reminder_write_rejected")

    def _require_occurrence(self, occurrence_id: int) -> None:
        if int(occurrence_id) != self._state.occurrence_id:
            raise RuntimeError("live_acceptance_occurrence_write_rejected")


@contextmanager
def _scoped_composition(
    state: FixtureState,
    business_date: date,
    expected_kind: str,
) -> Iterator[None]:
    repository_class = reminder_cloud.SQLiteReminderRepository

    def factory(connections, *, clock):
        return _ScopedFixtureRepository(
            repository_class(connections, clock=clock),
            state,
            business_date,
            expected_kind,
        )

    reminder_cloud.SQLiteReminderRepository = factory
    try:
        yield
    finally:
        reminder_cloud.SQLiteReminderRepository = repository_class


def _clock_for(business_date: date):
    instant = datetime.combine(business_date, time(10, 0), tzinfo=SHANGHAI)
    return lambda: instant.timestamp()


def _assert_only_expected_fixture_mark_changed(
    before: list[dict],
    after: list[dict],
    state: FixtureState,
    expected_kind: str,
) -> None:
    if len(before) != 1 or len(after) != 1:
        raise RuntimeError("live_acceptance_occurrence_scope_changed")
    first = before[0]
    second = after[0]
    if int(first["id"]) != state.occurrence_id or int(second["id"]) != state.occurrence_id:
        raise RuntimeError("live_acceptance_occurrence_scope_changed")
    mark_column = MARK_COLUMNS[expected_kind]
    if first[mark_column] is not None or not second[mark_column]:
        raise RuntimeError("live_acceptance_expected_mark_missing")
    allowed = {mark_column, "updated_at"}
    changed = {key for key in first if first[key] != second[key]}
    if not changed or not changed.issubset(allowed):
        raise RuntimeError("live_acceptance_fixture_change_invalid")


def run_multi_node(
    state_path: Path,
    *,
    database: Path,
    fixture_id: int,
    business_date: date,
    expect_topic: str,
) -> dict[str, object]:
    state = load_fixture_state(state_path, fixture_id)
    resolved_database = database.resolve(strict=False)
    if (
        resolved_database != reminder_cloud.FORMAL_DATABASE_PATH.resolve(strict=False)
        or resolved_database != state.database.resolve(strict=False)
        or state.topic != reminder_cloud.PRODUCTION_NTFY_TOPIC
        or expect_topic != reminder_cloud.PRODUCTION_NTFY_TOPIC
        or os.environ.get(reminder_cloud.PRODUCTION_NTFY_TOPIC_ENV) != expect_topic
    ):
        raise RuntimeError("live_acceptance_production_target_invalid")
    expected_kind = _expected_kind(state, business_date)
    _validate_fixture(database, state)
    _, before_occurrences = _read_fixture(database, state)
    before_digests = _unscoped_table_digests(database, state.reminder_id)
    clock = _clock_for(business_date)
    with _scoped_composition(state, business_date, expected_kind):
        first = reminder_cloud.run_schedule_once(database, clock=clock)
        _, after_first_occurrences = _read_fixture(database, state)
        second = reminder_cloud.run_schedule_once(database, clock=clock)
    _, after_second_occurrences = _read_fixture(database, state)
    after_digests = _unscoped_table_digests(database, state.reminder_id)
    if first != 1 or second != 0:
        raise RuntimeError("live_acceptance_send_or_dedupe_failed")
    _assert_only_expected_fixture_mark_changed(
        before_occurrences,
        after_first_occurrences,
        state,
        expected_kind,
    )
    if after_first_occurrences != after_second_occurrences:
        raise RuntimeError("live_acceptance_duplicate_pass_changed_fixture")
    if before_digests != after_digests:
        changed = sorted(set(before_digests) | set(after_digests))
        changed = [name for name in changed if before_digests.get(name) != after_digests.get(name)]
        raise RuntimeError("live_acceptance_unscoped_tables_changed:" + ",".join(changed))
    return {
        "production_topic": expect_topic,
        "node": expected_kind,
        "first_pass_sent": first,
        "duplicate_pass_sent": second,
        "fixture_mark_persisted": True,
        "unscoped_tables_unchanged": True,
    }


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser()
    subparsers = parser.add_subparsers(dest="command", required=True)
    multi = subparsers.add_parser("multi")
    multi.add_argument("--database", type=Path, required=True)
    multi.add_argument("--fixture-state", type=Path, required=True)
    multi.add_argument("--fixture-id", type=int, required=True)
    multi.add_argument("--business-date", type=date.fromisoformat, required=True)
    multi.add_argument("--expect-topic", required=True)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    if args.command == "multi":
        result = run_multi_node(
            args.fixture_state,
            database=args.database,
            fixture_id=args.fixture_id,
            business_date=args.business_date,
            expect_topic=args.expect_topic,
        )
        print(json.dumps(result, sort_keys=True, separators=(",", ":")))
        return 0
    raise RuntimeError("live_acceptance_command_invalid")


if __name__ == "__main__":
    raise SystemExit(main())
