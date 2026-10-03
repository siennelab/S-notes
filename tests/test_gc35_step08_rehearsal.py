from __future__ import annotations

import sqlite3
import tempfile
import unittest
from contextlib import closing
from datetime import date, datetime
from pathlib import Path

from scripts.gc35_step08_rehearsal import (
    BUSINESS_SETTING_KEYS,
    EXCLUDED_REMINDER_SETTING_KEYS,
    REMINDER_TABLES,
    RehearsalError,
    create_consistent_snapshot,
    initialize_candidate,
    migrate_snapshot,
    no_occurrence_business_path_reachable,
    reconcile,
    run_rehearsal,
)
from src.snotes.application.reminder_service import ReminderService
from src.snotes.domain.reminder_schedule import SHANGHAI
from src.snotes.domain.reminders import REMINDER_ACTIVE, ReminderDraft, ReminderSettings
from src.snotes.storage.repositories.reminders import SQLiteReminderRepository
from src.snotes.storage.schema import initialize_database


AS_OF = date(2026, 10, 2)
NOW = datetime(2026, 10, 2, 12, 0, tzinfo=SHANGHAI)


class Step08RehearsalTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory(prefix="snotes-step08a-test-")
        self.root = Path(self.temporary.name)
        self.source = self.root / "formal.db"
        self.snapshot = self.root / "snapshot.db"
        self.candidate = self.root / "candidate.db"
        connections = initialize_database(self.source)
        repository = SQLiteReminderRepository(connections, clock=lambda: NOW)
        service = ReminderService(repository, clock=lambda: NOW.timestamp())
        repository.save_settings(
            ReminderSettings(
                notifications_enabled=True,
                daily_time="09:35",
                ntfy_endpoint="https://private.invalid",
                ntfy_topic="private-topic",
            )
        )
        repository.set_text_setting("reminder/last_test_topic", "private-test-topic")
        repository.set_text_setting("reminder/last_scheduled_date", "2026-10-01")
        table = service.create_table("FICTIONAL migration table")
        self.multi = service.create_reminder(
            ReminderDraft(
                title="FICTIONAL multi reminder",
                lifecycle=REMINDER_ACTIVE,
                action_spec={"type": "one_time", "date": "2026-10-12"},
                deadline_spec={"type": "offset_days", "days": 2},
                note="FICTIONAL private-like body",
                table_id=table.id,
                multi_notify=True,
            )
        )
        self.completed = service.create_reminder(
            ReminderDraft(
                title="FICTIONAL completed one-time",
                lifecycle=REMINDER_ACTIVE,
                action_spec={"type": "one_time", "date": "2026-10-03"},
                table_id=table.id,
            )
        )
        service.ai_complete_item(
            actor_target_id="fictional-ai",
            request_id="fictional-completed-one-time",
            item_id=self.completed.id,
        )
        with connections.write() as database:
            database.execute(
                "INSERT INTO ai_operations "
                "(request_id, actor_target_id, operation_type, status, result_json, created_at, updated_at) "
                "VALUES ('shared-operation', 'fictional', 'note', 'ok', '{}', ?, ?)",
                (NOW.isoformat(), NOW.isoformat()),
            )
            database.execute(
                "INSERT INTO ai_reminder_operations "
                "(happened_at, actor_target_id, action, table_id, table_title, item_id, "
                "item_title, label_id, label_name, request_id, changed_fields_json, before_json, after_json) "
                "VALUES (?, 'fictional', 'update', ?, 'FICTIONAL migration table', ?, "
                "'FICTIONAL multi reminder', NULL, '', 'reminder-operation', '[\"note\"]', '{}', '{}')",
                (NOW.isoformat(), table.id, self.multi.id),
            )
            database.execute("ALTER TABLE reminders ADD COLUMN label_id INTEGER")
            database.execute("ALTER TABLE reminders ADD COLUMN ai_visible INTEGER")
            database.execute(
                "UPDATE reminders SET label_id = ("
                "SELECT reminder_tables.label_id FROM reminder_tables "
                "WHERE reminder_tables.id = reminders.table_id), "
                "ai_visible = (SELECT reminder_tables.ai_visible FROM reminder_tables "
                "WHERE reminder_tables.id = reminders.table_id)"
            )
            database.execute(
                "UPDATE reminders SET deleted_at = ?, trash_group_id = ? WHERE id = ?",
                ("2026-09-30T08:00:00+08:00", "legacy-item-group", self.multi.id),
            )
            database.execute(
                "UPDATE schema_meta SET meta_value = '4' WHERE meta_key = 'reminder_schema_version'"
            )

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def test_full_rehearsal_migrates_exact_reminder_state_only(self) -> None:
        before = self.source.read_bytes()

        result = run_rehearsal(
            self.source,
            self.snapshot,
            self.candidate,
            as_of=AS_OF,
        )

        self.assertTrue(result["pass"])
        self.assertEqual(result["schema_guard"]["source_marker"], "4")
        self.assertTrue(result["schema_guard"]["structurally_compatible"])
        self.assertFalse(result["schema_guard"]["stale_source_marker"])
        self.assertEqual(result["total_diff"], 0)
        self.assertGreaterEqual(result["schedule"]["probe_count"], 5)
        self.assertEqual(result["schedule"]["diff"], 0)
        self.assertEqual(result["reminder_semantic_mappings"]["diff"], 0)
        self.assertTrue(result["no_occurrence"]["observed_in_snapshot"])
        self.assertTrue(result["no_occurrence"]["reachable"])
        self.assertTrue(result["no_occurrence"]["candidate_parity"])
        self.assertEqual(
            result["no_occurrence"]["reachability_path"],
            "ai_complete_item_current_occurrence_only",
        )
        self.assertEqual(self.source.read_bytes(), before)
        with closing(sqlite3.connect(self.candidate)) as database:
            migrated_keys = {
                row[0]
                for row in database.execute(
                    "SELECT setting_key FROM app_settings WHERE setting_key LIKE 'reminder/%'"
                )
            }
            self.assertEqual(migrated_keys, BUSINESS_SETTING_KEYS)
            self.assertFalse(migrated_keys & EXCLUDED_REMINDER_SETTING_KEYS)
            self.assertEqual(database.execute("SELECT COUNT(*) FROM ai_operations").fetchone()[0], 0)
            self.assertEqual(database.execute("SELECT COUNT(*) FROM notes").fetchone()[0], 0)
            history = database.execute(
                "SELECT deleted_at, trash_group_id FROM reminders WHERE id = ?",
                (self.multi.id,),
            ).fetchone()
            self.assertEqual(
                history,
                ("2026-09-30T08:00:00+08:00", "legacy-item-group"),
            )
            self.assertNotIn(
                "label_id",
                {row[1] for row in database.execute("PRAGMA table_info(reminders)")},
            )
            self.assertNotIn(
                "ai_visible",
                {row[1] for row in database.execute("PRAGMA table_info(reminders)")},
            )

    def test_no_occurrence_is_reachable_through_ai_current_completion(self) -> None:
        self.assertTrue(no_occurrence_business_path_reachable())

    def test_unknown_source_field_fails_before_candidate_changes(self) -> None:
        with closing(sqlite3.connect(self.source)) as database, database:
            database.execute("ALTER TABLE reminders ADD COLUMN fictional_incompatible TEXT")
        create_consistent_snapshot(self.source, self.snapshot)
        initialize_candidate(self.candidate)

        with self.assertRaisesRegex(
            RehearsalError,
            "unknown_source_reminder_field",
        ):
            migrate_snapshot(self.snapshot, self.candidate)

        with closing(sqlite3.connect(self.candidate)) as database:
            self.assertEqual(database.execute("SELECT COUNT(*) FROM reminders").fetchone()[0], 0)
            self.assertEqual(database.execute("SELECT COUNT(*) FROM reminder_labels").fetchone()[0], 1)

    def test_default_only_legacy_ai_operation_fields_are_omitted(self) -> None:
        with closing(sqlite3.connect(self.source)) as database, database:
            database.execute(
                "ALTER TABLE ai_reminder_operations ADD COLUMN reminder_id INTEGER"
            )
            database.execute(
                "ALTER TABLE ai_reminder_operations "
                "ADD COLUMN reminder_title TEXT NOT NULL DEFAULT ''"
            )
        create_consistent_snapshot(self.source, self.snapshot)
        initialize_candidate(self.candidate)

        migrate_snapshot(self.snapshot, self.candidate)
        result = reconcile(self.snapshot, self.candidate, as_of=AS_OF)

        self.assertTrue(result["pass"])
        self.assertEqual(result["tables"]["ai_reminder_operations"]["diff"], 0)
        with closing(sqlite3.connect(self.candidate)) as database:
            columns = {
                row[1]
                for row in database.execute(
                    "PRAGMA table_info(ai_reminder_operations)"
                )
            }
            self.assertNotIn("reminder_id", columns)
            self.assertNotIn("reminder_title", columns)

    def test_non_default_legacy_ai_operation_reminder_id_is_rejected(self) -> None:
        with closing(sqlite3.connect(self.source)) as database, database:
            database.execute(
                "ALTER TABLE ai_reminder_operations ADD COLUMN reminder_id INTEGER"
            )
            database.execute(
                "UPDATE ai_reminder_operations SET reminder_id = ? WHERE request_id = ?",
                (self.multi.id, "reminder-operation"),
            )
        create_consistent_snapshot(self.source, self.snapshot)
        initialize_candidate(self.candidate)

        with self.assertRaisesRegex(
            RehearsalError,
            "legacy_ai_reminder_operation_reminder_id_non_default",
        ):
            migrate_snapshot(self.snapshot, self.candidate)
        with self.assertRaisesRegex(
            RehearsalError,
            "legacy_ai_reminder_operation_reminder_id_non_default",
        ):
            reconcile(self.snapshot, self.candidate, as_of=AS_OF)

        with closing(sqlite3.connect(self.candidate)) as database:
            self.assertEqual(
                database.execute(
                    "SELECT COUNT(*) FROM ai_reminder_operations"
                ).fetchone()[0],
                0,
            )

    def test_non_default_legacy_ai_operation_reminder_title_is_rejected(self) -> None:
        with closing(sqlite3.connect(self.source)) as database, database:
            database.execute(
                "ALTER TABLE ai_reminder_operations ADD COLUMN reminder_title "
                "TEXT COLLATE RTRIM NOT NULL DEFAULT ''"
            )
            database.execute(
                "UPDATE ai_reminder_operations SET reminder_title = ? WHERE request_id = ?",
                (" ", "reminder-operation"),
            )
        create_consistent_snapshot(self.source, self.snapshot)
        initialize_candidate(self.candidate)

        with self.assertRaisesRegex(
            RehearsalError,
            "legacy_ai_reminder_operation_reminder_title_non_default",
        ):
            migrate_snapshot(self.snapshot, self.candidate)
        with self.assertRaisesRegex(
            RehearsalError,
            "legacy_ai_reminder_operation_reminder_title_non_default",
        ):
            reconcile(self.snapshot, self.candidate, as_of=AS_OF)

    def test_null_legacy_ai_operation_reminder_title_is_rejected(self) -> None:
        with closing(sqlite3.connect(self.source)) as database, database:
            database.execute(
                "ALTER TABLE ai_reminder_operations ADD COLUMN reminder_title TEXT"
            )
        create_consistent_snapshot(self.source, self.snapshot)
        initialize_candidate(self.candidate)

        with self.assertRaisesRegex(
            RehearsalError,
            "legacy_ai_reminder_operation_reminder_title_non_default",
        ):
            migrate_snapshot(self.snapshot, self.candidate)

    def test_unknown_ai_operation_source_field_still_rejects(self) -> None:
        with closing(sqlite3.connect(self.source)) as database, database:
            database.execute(
                "ALTER TABLE ai_reminder_operations ADD COLUMN fictional_unknown TEXT"
            )
        create_consistent_snapshot(self.source, self.snapshot)
        initialize_candidate(self.candidate)

        with self.assertRaisesRegex(
            RehearsalError,
            "unknown_source_ai_reminder_operation_field",
        ):
            migrate_snapshot(self.snapshot, self.candidate)

    def test_matching_legacy_label_maps_through_table(self) -> None:
        create_consistent_snapshot(self.source, self.snapshot)
        initialize_candidate(self.candidate)

        migrate_snapshot(self.snapshot, self.candidate)
        result = reconcile(self.snapshot, self.candidate, as_of=AS_OF)

        self.assertEqual(result["reminder_semantic_mappings"]["diff"], 0)
        self.assertTrue(result["pass"])

    def test_reordered_source_columns_use_name_based_contract(self) -> None:
        with closing(sqlite3.connect(self.source)) as database:
            database.execute("PRAGMA foreign_keys = OFF")
            database.execute(
                """
                CREATE TABLE reminders_reordered (
                    id INTEGER PRIMARY KEY,
                    title TEXT NOT NULL,
                    lifecycle TEXT NOT NULL DEFAULT 'active',
                    action_spec_json TEXT NOT NULL,
                    deadline_spec_json TEXT NOT NULL DEFAULT '',
                    note TEXT NOT NULL DEFAULT '',
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    completed_at TEXT,
                    label_id INTEGER,
                    ai_visible INTEGER,
                    deleted_at TEXT,
                    trash_group_id TEXT,
                    table_id INTEGER REFERENCES reminder_tables(id),
                    multi_notify INTEGER NOT NULL DEFAULT 0,
                    recurrence_mode TEXT
                )
                """
            )
            columns = (
                "id, title, lifecycle, action_spec_json, deadline_spec_json, note, "
                "created_at, updated_at, completed_at, label_id, ai_visible, "
                "deleted_at, trash_group_id, table_id, multi_notify, recurrence_mode"
            )
            database.execute(
                f"INSERT INTO reminders_reordered ({columns}) SELECT {columns} FROM reminders"
            )
            database.execute("DROP TABLE reminders")
            database.execute("ALTER TABLE reminders_reordered RENAME TO reminders")
            database.commit()
        create_consistent_snapshot(self.source, self.snapshot)
        initialize_candidate(self.candidate)

        migrate_snapshot(self.snapshot, self.candidate)
        result = reconcile(self.snapshot, self.candidate, as_of=AS_OF)

        self.assertTrue(result["pass"])
        self.assertEqual(result["tables"]["reminders"]["diff"], 0)

    def test_marker3_matching_structure_and_reordered_operation_columns_pass(self) -> None:
        with closing(sqlite3.connect(self.source)) as database:
            database.execute(
                "UPDATE schema_meta SET meta_value = '3' "
                "WHERE meta_key = 'reminder_schema_version'"
            )
            database.execute(
                """
                CREATE TABLE ai_reminder_operations_reordered (
                    request_id TEXT NOT NULL,
                    id INTEGER PRIMARY KEY,
                    happened_at TEXT NOT NULL,
                    action TEXT NOT NULL,
                    actor_target_id TEXT NOT NULL,
                    item_id INTEGER,
                    table_id INTEGER,
                    table_title TEXT NOT NULL DEFAULT '',
                    item_title TEXT NOT NULL DEFAULT '',
                    label_id INTEGER,
                    label_name TEXT NOT NULL DEFAULT '',
                    changed_fields_json TEXT NOT NULL DEFAULT '[]',
                    before_json TEXT NOT NULL DEFAULT '{}',
                    after_json TEXT NOT NULL DEFAULT '{}'
                )
                """
            )
            columns = (
                "request_id, id, happened_at, action, actor_target_id, item_id, "
                "table_id, table_title, item_title, label_id, label_name, "
                "changed_fields_json, before_json, after_json"
            )
            database.execute(
                f"INSERT INTO ai_reminder_operations_reordered ({columns}) "
                f"SELECT {columns} FROM ai_reminder_operations"
            )
            database.execute("DROP TABLE ai_reminder_operations")
            database.execute(
                "ALTER TABLE ai_reminder_operations_reordered "
                "RENAME TO ai_reminder_operations"
            )
            database.commit()

        result = run_rehearsal(
            self.source,
            self.snapshot,
            self.candidate,
            as_of=AS_OF,
        )

        self.assertTrue(result["pass"])
        self.assertEqual(result["schema_guard"]["source_marker"], "3")
        self.assertTrue(result["schema_guard"]["structurally_compatible"])
        self.assertTrue(result["schema_guard"]["stale_source_marker"])
        self.assertEqual(result["tables"]["ai_reminder_operations"]["diff"], 0)

    def test_marker3_missing_3_to_4_field_is_rejected(self) -> None:
        with closing(sqlite3.connect(self.source)) as database, database:
            database.execute(
                "UPDATE schema_meta SET meta_value = '3' "
                "WHERE meta_key = 'reminder_schema_version'"
            )
            database.execute(
                "ALTER TABLE reminder_occurrences DROP COLUMN advance_7_notified_at"
            )
        create_consistent_snapshot(self.source, self.snapshot)
        initialize_candidate(self.candidate)

        with self.assertRaisesRegex(
            RehearsalError,
            "reminder_schema_columns_incompatible:reminder_occurrences",
        ):
            migrate_snapshot(self.snapshot, self.candidate)

    def test_marker3_changed_unique_constraint_is_rejected(self) -> None:
        with closing(sqlite3.connect(self.source)) as database, database:
            database.execute(
                "UPDATE schema_meta SET meta_value = '3' "
                "WHERE meta_key = 'reminder_schema_version'"
            )
            database.execute("DROP INDEX idx_reminder_labels_active_name")
            database.execute(
                "CREATE UNIQUE INDEX idx_reminder_labels_active_name "
                "ON reminder_labels(name COLLATE NOCASE) WHERE is_deleted = 0"
            )
        create_consistent_snapshot(self.source, self.snapshot)
        initialize_candidate(self.candidate)

        with self.assertRaisesRegex(
            RehearsalError,
            "reminder_schema_unique_constraints_incompatible:reminder_labels",
        ):
            migrate_snapshot(self.snapshot, self.candidate)

    def test_marker3_unknown_field_is_rejected(self) -> None:
        with closing(sqlite3.connect(self.source)) as database, database:
            database.execute(
                "UPDATE schema_meta SET meta_value = '3' "
                "WHERE meta_key = 'reminder_schema_version'"
            )
            database.execute(
                "ALTER TABLE reminder_occurrences ADD COLUMN fictional_unknown TEXT"
            )
        create_consistent_snapshot(self.source, self.snapshot)
        initialize_candidate(self.candidate)

        with self.assertRaisesRegex(
            RehearsalError,
            "reminder_schema_columns_incompatible:reminder_occurrences",
        ):
            migrate_snapshot(self.snapshot, self.candidate)

    def test_unknown_marker_is_rejected_even_with_matching_structure(self) -> None:
        with closing(sqlite3.connect(self.source)) as database, database:
            database.execute(
                "UPDATE schema_meta SET meta_value = '2' "
                "WHERE meta_key = 'reminder_schema_version'"
            )
        create_consistent_snapshot(self.source, self.snapshot)
        initialize_candidate(self.candidate)

        with self.assertRaisesRegex(
            RehearsalError,
            "reminder_schema_version_incompatible",
        ):
            migrate_snapshot(self.snapshot, self.candidate)

    def test_missing_target_canonical_column_is_rejected(self) -> None:
        create_consistent_snapshot(self.source, self.snapshot)
        initialize_candidate(self.candidate)
        with closing(sqlite3.connect(self.candidate)) as database, database:
            database.execute("ALTER TABLE reminders DROP COLUMN deleted_at")

        with self.assertRaisesRegex(
            RehearsalError,
            "candidate_reminder_schema_contract_incompatible",
        ):
            migrate_snapshot(self.snapshot, self.candidate)

    def test_legacy_label_conflict_is_rejected_before_candidate_changes(self) -> None:
        with closing(sqlite3.connect(self.source)) as database, database:
            cursor = database.execute(
                "INSERT INTO reminder_labels "
                "(label_key, name, sort_order, is_deleted, created_at, updated_at) "
                "VALUES ('other-valid-label', 'OTHER VALID LABEL', 99, 0, ?, ?)",
                (NOW.isoformat(), NOW.isoformat()),
            )
            database.execute(
                "UPDATE reminders SET label_id = ? WHERE id = ?",
                (int(cursor.lastrowid), self.multi.id),
            )
        create_consistent_snapshot(self.source, self.snapshot)
        initialize_candidate(self.candidate)

        with self.assertRaisesRegex(
            RehearsalError,
            "legacy_reminder_label_mapping_conflict",
        ):
            migrate_snapshot(self.snapshot, self.candidate)

        with closing(sqlite3.connect(self.candidate)) as database:
            self.assertEqual(database.execute("SELECT COUNT(*) FROM reminders").fetchone()[0], 0)

    def test_legacy_ai_visibility_expansion_is_rejected(self) -> None:
        with closing(sqlite3.connect(self.source)) as database, database:
            database.execute(
                "UPDATE reminders SET ai_visible = 0 WHERE id = ?",
                (self.multi.id,),
            )
        create_consistent_snapshot(self.source, self.snapshot)
        initialize_candidate(self.candidate)

        with self.assertRaisesRegex(
            RehearsalError,
            "legacy_reminder_ai_visibility_expansion",
        ):
            migrate_snapshot(self.snapshot, self.candidate)

    def test_history_columns_do_not_change_current_reminder_runtime(self) -> None:
        connections = initialize_database(self.source)
        repository = SQLiteReminderRepository(connections, clock=lambda: NOW)
        service = ReminderService(repository, clock=lambda: NOW.timestamp())

        item = service.get_reminder(self.multi.id)

        self.assertIsNotNone(item)
        self.assertEqual(item.reminder.id, self.multi.id)
        self.assertEqual(item.reminder.lifecycle, REMINDER_ACTIVE)
        self.assertIsNotNone(item.occurrence)

    def test_history_nulls_are_preserved_and_tampering_is_detected(self) -> None:
        create_consistent_snapshot(self.source, self.snapshot)
        initialize_candidate(self.candidate)
        migrate_snapshot(self.snapshot, self.candidate)
        with closing(sqlite3.connect(self.candidate)) as database:
            self.assertEqual(
                database.execute(
                    "SELECT deleted_at, trash_group_id FROM reminders WHERE id = ?",
                    (self.completed.id,),
                ).fetchone(),
                (None, None),
            )
        with closing(sqlite3.connect(self.candidate)) as database, database:
            database.execute(
                "UPDATE reminders SET deleted_at = NULL, trash_group_id = NULL WHERE id = ?",
                (self.multi.id,),
            )

        result = reconcile(self.snapshot, self.candidate, as_of=AS_OF)

        self.assertFalse(result["pass"])
        difference = next(
            row
            for row in result["tables"]["reminders"]["differences"]
            if row["identity"] == [self.multi.id]
        )
        self.assertEqual(difference["fields"], ["deleted_at", "trash_group_id"])

    def test_reconciliation_reports_identity_and_fields_without_values(self) -> None:
        create_consistent_snapshot(self.source, self.snapshot)
        initialize_candidate(self.candidate)
        migrate_snapshot(self.snapshot, self.candidate)
        with closing(sqlite3.connect(self.candidate)) as database, database:
            database.execute(
                "UPDATE reminders SET action_spec_json = ? WHERE id = ?",
                ('{"type":"one_time","date":"2026-10-13"}', self.multi.id),
            )

        result = reconcile(self.snapshot, self.candidate, as_of=AS_OF)

        self.assertFalse(result["pass"])
        self.assertEqual(result["tables"]["reminders"]["diff"], 1)
        difference = result["tables"]["reminders"]["differences"][0]
        self.assertEqual(difference["identity"], [self.multi.id])
        self.assertEqual(difference["fields"], ["action_spec_json"])
        self.assertNotIn("FICTIONAL", repr(result))

    def test_unknown_reminder_setting_is_rejected_without_disclosure(self) -> None:
        with closing(sqlite3.connect(self.source)) as database, database:
            database.execute(
                "INSERT INTO app_settings VALUES (?, ?, ?)",
                ("reminder/future_private_setting", "private-value", NOW.isoformat()),
            )
        create_consistent_snapshot(self.source, self.snapshot)
        initialize_candidate(self.candidate)

        with self.assertRaisesRegex(RehearsalError, "unclassified_reminder_setting_key") as raised:
            migrate_snapshot(self.snapshot, self.candidate)
        self.assertNotIn("future_private", str(raised.exception))
        self.assertNotIn("private-value", str(raised.exception))


if __name__ == "__main__":
    unittest.main()
