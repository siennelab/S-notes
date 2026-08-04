from __future__ import annotations

import hashlib
import json
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import src.database as database_module
from src.ai_targets import AI_TARGET_DEFINITIONS
from src.database import (
    _migrate_ai_visibility_values,
    _seed_ai_settings,
    _seed_ai_targets,
    create_note,
    initialize_database,
    set_note_ai_targets,
)


class TrackingConnection(sqlite3.Connection):
    recorded_total_changes: int | None = None

    def close(self) -> None:
        if self.recorded_total_changes is None:
            self.recorded_total_changes = self.total_changes
        super().close()


class IdempotentAiTargetSeedingTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.database_path = Path(self.temporary_directory.name) / "s_notes.db"

    def tearDown(self) -> None:
        self.temporary_directory.cleanup()

    def test_first_initialize_inserts_all_required_seed_data(self) -> None:
        initialize_database(self.database_path)

        rows = self._target_snapshot()
        self.assertEqual(len(rows), len(AI_TARGET_DEFINITIONS))
        self.assertEqual(
            [row[:5] for row in rows],
            sorted(
                [
                    (
                        item.target_id,
                        item.display_name,
                        item.slug,
                        1 if item.system else 0,
                        1,
                    )
                    for item in AI_TARGET_DEFINITIONS
                ],
                key=lambda row: row[0],
            ),
        )
        self.assertTrue(all(row[5] and row[6] for row in rows))

        schema_meta = {row[1]: row[2] for row in self._schema_meta_snapshot()}
        self.assertEqual(schema_meta["ai_foundation_version"], "2")
        report = json.loads(schema_meta["task13a_last_migration_report"])
        self.assertEqual(report["schema_version"], 2)

        settings = {row[1]: row[2] for row in self._app_settings_snapshot()}
        self.assertEqual(
            settings,
            {
                "ai_create_requires_confirmation": "0",
                "ai_modify_requires_confirmation": "1",
            },
        )

    def test_second_and_third_initialize_have_zero_mutations_and_stable_hash(self) -> None:
        initialize_database(self.database_path)
        note = create_note(self.database_path)
        set_note_ai_targets(note["id"], ["common", "gale"], self.database_path)
        with sqlite3.connect(self.database_path) as connection:
            connection.execute(
                """
                INSERT INTO ai_receipt_inbox(
                    raw_text, ai_visibility, created_at, updated_at
                ) VALUES (?, ?, ?, ?)
                """,
                ("fictional receipt", "full", "2000-01-01", "2000-01-02"),
            )
            connection.execute(
                """
                UPDATE app_settings
                SET setting_value = ?, updated_at = ?
                WHERE setting_key = ?
                """,
                (
                    "1",
                    "2000-01-03",
                    "ai_create_requires_confirmation",
                ),
            )

        first_state = self._stable_state()
        first_hash = self._database_sha256()
        first_sidecars = self._sidecar_state()

        second_changes, second_trace = self._tracked_initialize()
        second_state = self._stable_state()
        second_hash = self._database_sha256()
        second_sidecars = self._sidecar_state()

        third_changes, third_trace = self._tracked_initialize()
        third_state = self._stable_state()
        third_hash = self._database_sha256()
        third_sidecars = self._sidecar_state()

        self.assertEqual(second_changes, 0)
        self.assertEqual(third_changes, 0)
        self.assertEqual(second_state, first_state)
        self.assertEqual(third_state, first_state)
        self.assertEqual(second_hash, first_hash)
        self.assertEqual(third_hash, first_hash)
        self.assertEqual(second_sidecars, first_sidecars)
        self.assertEqual(third_sidecars, first_sidecars)
        self.assertEqual(self._target_table_mutations(second_trace), [])
        self.assertEqual(self._target_table_mutations(third_trace), [])
        self._assert_database_integrity()

    def test_only_stale_target_is_updated(self) -> None:
        initialize_database(self.database_path)
        stale_target = AI_TARGET_DEFINITIONS[1]
        stale_timestamp = "2000-01-01T00:00:00+08:00"
        with sqlite3.connect(self.database_path) as connection:
            connection.execute(
                """
                UPDATE ai_targets
                SET display_name = ?, slug = ?, is_system = ?,
                    is_enabled = ?, updated_at = ?
                WHERE target_id = ?
                """,
                (
                    "OUTDATED DISPLAY",
                    "outdated_slug",
                    1 if not stale_target.system else 0,
                    0,
                    stale_timestamp,
                    stale_target.target_id,
                ),
            )

        before = {row[0]: row for row in self._target_snapshot()}
        with sqlite3.connect(self.database_path) as connection:
            changes_before = connection.total_changes
            _seed_ai_targets(connection)
            changed_rows = connection.total_changes - changes_before
        after = {row[0]: row for row in self._target_snapshot()}

        self.assertEqual(changed_rows, 1)
        self.assertEqual(
            after[stale_target.target_id][1:5],
            (
                stale_target.display_name,
                stale_target.slug,
                1 if stale_target.system else 0,
                1,
            ),
        )
        self.assertNotEqual(after[stale_target.target_id][6], stale_timestamp)
        for target_id in before.keys() - {stale_target.target_id}:
            self.assertEqual(after[target_id], before[target_id])

    def test_schema_meta_updates_only_stale_value_without_replacing_row(self) -> None:
        initialize_database(self.database_path)
        with sqlite3.connect(self.database_path) as connection:
            connection.execute(
                "UPDATE schema_meta SET meta_value = ? WHERE meta_key = ?",
                ("outdated", "ai_foundation_version"),
            )

        before = {row[1]: row for row in self._schema_meta_snapshot()}
        with sqlite3.connect(self.database_path) as connection:
            connection.row_factory = sqlite3.Row
            changes_before = connection.total_changes
            database_module._set_schema_meta(
                connection,
                "ai_foundation_version",
                "2",
            )
            changed_rows = connection.total_changes - changes_before
        after = {row[1]: row for row in self._schema_meta_snapshot()}

        self.assertEqual(changed_rows, 1)
        self.assertEqual(after["ai_foundation_version"][0], before["ai_foundation_version"][0])
        self.assertEqual(after["ai_foundation_version"][2], "2")
        self.assertEqual(
            after["task13a_last_migration_report"],
            before["task13a_last_migration_report"],
        )

        with sqlite3.connect(self.database_path) as connection:
            connection.row_factory = sqlite3.Row
            trace: list[str] = []
            connection.set_trace_callback(trace.append)
            changes_before = connection.total_changes
            database_module._set_schema_meta(
                connection,
                "ai_foundation_version",
                "2",
            )
            self.assertEqual(connection.total_changes - changes_before, 0)
        self.assertEqual(self._target_table_mutations(trace), [])

    def test_inbox_visibility_updates_only_noncanonical_rows(self) -> None:
        inbox_path = Path(self.temporary_directory.name) / "inbox.db"
        values = (
            "full",
            "redacted",
            "hidden",
            "可直接给 AI",
            "脱敏后给 AI",
            "AI完整可见",
            "AI脱敏后可见",
            None,
            "",
            "unknown legacy value",
        )
        with sqlite3.connect(inbox_path) as connection:
            connection.executescript(
                """
                CREATE TABLE notes(id INTEGER PRIMARY KEY, ai_visibility TEXT);
                CREATE TABLE ai_migration_issues(
                    issue_type TEXT,
                    source_value TEXT,
                    note_id INTEGER,
                    recorded_at TEXT,
                    UNIQUE(issue_type, source_value, note_id)
                );
                CREATE TABLE ai_receipt_inbox(
                    id INTEGER PRIMARY KEY,
                    ai_visibility TEXT,
                    status TEXT,
                    note_id INTEGER,
                    operation_payload TEXT,
                    actor_target_id TEXT,
                    updated_at TEXT
                );
                """
            )
            for index, value in enumerate(values, start=1):
                connection.execute(
                    """
                    INSERT INTO ai_receipt_inbox(
                        id, ai_visibility, status, note_id,
                        operation_payload, actor_target_id, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        index,
                        value,
                        "pending",
                        index,
                        f"fictional-{index}",
                        "gale",
                        f"2000-01-{index:02d}",
                    ),
                )

        with sqlite3.connect(inbox_path) as connection:
            connection.row_factory = sqlite3.Row
            before = connection.execute(
                "SELECT * FROM ai_receipt_inbox ORDER BY id"
            ).fetchall()
            changes_before = connection.total_changes
            _migrate_ai_visibility_values(connection)
            changed_rows = connection.total_changes - changes_before
            after = connection.execute(
                "SELECT * FROM ai_receipt_inbox ORDER BY id"
            ).fetchall()
            stable_trace: list[str] = []
            connection.set_trace_callback(stable_trace.append)
            changes_before_stable = connection.total_changes
            _migrate_ai_visibility_values(connection)
            stable_changes = connection.total_changes - changes_before_stable

        self.assertEqual(changed_rows, 7)
        self.assertEqual(stable_changes, 0)
        self.assertEqual([tuple(row) for row in after[:3]], [tuple(row) for row in before[:3]])
        self.assertEqual(
            [row["ai_visibility"] for row in after],
            [
                "full",
                "redacted",
                "hidden",
                "full",
                "redacted",
                "full",
                "redacted",
                "hidden",
                "hidden",
                "hidden",
            ],
        )
        for prior, current in zip(before, after):
            self.assertEqual(tuple(prior)[2:], tuple(current)[2:])
        self.assertEqual(self._target_table_mutations(stable_trace), [])

    def test_ai_settings_insert_only_missing_keys_and_preserve_existing_value(self) -> None:
        initialize_database(self.database_path)
        with sqlite3.connect(self.database_path) as connection:
            connection.execute(
                """
                UPDATE app_settings
                SET setting_value = ?, updated_at = ?
                WHERE setting_key = ?
                """,
                ("1", "2000-02-01", "ai_create_requires_confirmation"),
            )
            connection.execute(
                "DELETE FROM app_settings WHERE setting_key = ?",
                ("ai_modify_requires_confirmation",),
            )

        before = {row[1]: row for row in self._app_settings_snapshot()}
        with sqlite3.connect(self.database_path) as connection:
            connection.row_factory = sqlite3.Row
            changes_before = connection.total_changes
            _seed_ai_settings(connection)
            changed_rows = connection.total_changes - changes_before
        after = {row[1]: row for row in self._app_settings_snapshot()}

        self.assertEqual(changed_rows, 1)
        self.assertEqual(
            after["ai_create_requires_confirmation"],
            before["ai_create_requires_confirmation"],
        )
        self.assertEqual(after["ai_modify_requires_confirmation"][2], "1")

        with sqlite3.connect(self.database_path) as connection:
            connection.row_factory = sqlite3.Row
            trace: list[str] = []
            connection.set_trace_callback(trace.append)
            changes_before = connection.total_changes
            _seed_ai_settings(connection)
            self.assertEqual(connection.total_changes - changes_before, 0)
        self.assertEqual(self._target_table_mutations(trace), [])

    def test_initialize_preserves_note_ai_target_links(self) -> None:
        initialize_database(self.database_path)
        note = create_note(self.database_path)
        set_note_ai_targets(note["id"], ["common", "gale"], self.database_path)
        links_before = self._link_snapshot()

        initialize_database(self.database_path)
        links_after_second = self._link_snapshot()
        initialize_database(self.database_path)
        links_after_third = self._link_snapshot()

        self.assertEqual(links_after_second, links_before)
        self.assertEqual(links_after_third, links_before)

    def _tracked_initialize(self) -> tuple[int, list[str]]:
        connections: list[TrackingConnection] = []
        trace: list[str] = []

        def tracked_connect(
            database_path: Path = database_module.DATABASE_PATH,
        ) -> TrackingConnection:
            connection = sqlite3.connect(
                database_path,
                timeout=5.0,
                factory=TrackingConnection,
            )
            connection.row_factory = sqlite3.Row
            connection.execute("PRAGMA foreign_keys = ON")
            connection.execute("PRAGMA busy_timeout = 5000")
            connection.set_trace_callback(trace.append)
            connections.append(connection)
            return connection

        with mock.patch.object(database_module, "_connect", side_effect=tracked_connect):
            initialize_database(self.database_path)

        for connection in connections:
            connection.close()
        return (
            sum(connection.recorded_total_changes or 0 for connection in connections),
            trace,
        )

    def _stable_state(self) -> dict[str, list[tuple]]:
        return {
            "targets": self._target_snapshot(),
            "schema_meta": self._schema_meta_snapshot(),
            "settings": self._app_settings_snapshot(),
            "inbox": self._inbox_snapshot(),
            "links": self._link_snapshot(),
        }

    def _target_snapshot(self) -> list[tuple]:
        with sqlite3.connect(self.database_path) as connection:
            return connection.execute(
                """
                SELECT target_id, display_name, slug, is_system, is_enabled,
                       created_at, updated_at
                FROM ai_targets
                ORDER BY target_id
                """
            ).fetchall()

    def _schema_meta_snapshot(self) -> list[tuple]:
        with sqlite3.connect(self.database_path) as connection:
            return connection.execute(
                """
                SELECT rowid, meta_key, meta_value
                FROM schema_meta
                ORDER BY meta_key
                """
            ).fetchall()

    def _app_settings_snapshot(self) -> list[tuple]:
        with sqlite3.connect(self.database_path) as connection:
            return connection.execute(
                """
                SELECT rowid, setting_key, setting_value, updated_at
                FROM app_settings
                ORDER BY setting_key
                """
            ).fetchall()

    def _inbox_snapshot(self) -> list[tuple]:
        with sqlite3.connect(self.database_path) as connection:
            return connection.execute(
                "SELECT rowid, * FROM ai_receipt_inbox ORDER BY id"
            ).fetchall()

    def _link_snapshot(self) -> list[tuple]:
        with sqlite3.connect(self.database_path) as connection:
            return connection.execute(
                """
                SELECT id, note_id, target_id, created_at
                FROM note_ai_targets
                ORDER BY id
                """
            ).fetchall()

    def _database_sha256(self) -> str:
        return hashlib.sha256(self.database_path.read_bytes()).hexdigest()

    def _sidecar_state(self) -> dict[str, bool]:
        return {
            suffix: Path(f"{self.database_path}{suffix}").exists()
            for suffix in ("-wal", "-shm", "-journal")
        }

    def _assert_database_integrity(self) -> None:
        with sqlite3.connect(self.database_path) as connection:
            self.assertEqual(connection.execute("PRAGMA quick_check").fetchone()[0], "ok")
            self.assertEqual(connection.execute("PRAGMA foreign_key_check").fetchall(), [])

    @staticmethod
    def _target_table_mutations(statements: list[str]) -> list[str]:
        target_tables = (
            "ai_targets",
            "schema_meta",
            "ai_receipt_inbox",
            "app_settings",
        )
        mutations: list[str] = []
        for raw_statement in statements:
            statement = " ".join(raw_statement.split()).lower()
            if statement.startswith(("insert ", "update ", "delete ", "replace ")) and any(
                table in statement for table in target_tables
            ):
                mutations.append(statement)
        return mutations


if __name__ == "__main__":
    unittest.main()
