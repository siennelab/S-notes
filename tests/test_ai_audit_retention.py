"""Count-based retention for fictional, isolated AI call logs."""

from __future__ import annotations

import json
import os
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

from src.snotes.domain.ai_audit import AiAuditEvent
from src.snotes.storage.repositories.ai_audit import SQLiteAiAuditRepository
from src.snotes.storage.repositories.ai_mcp_operations import (
    SQLiteAiMcpOperationRepository,
)
from src.snotes.storage.repositories.tags import SQLiteTagsRepository
from src.snotes.storage.schema import initialize_database


class AiAuditRetentionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.home = tempfile.TemporaryDirectory(prefix="snotes-audit-retention-home-")
        cls.environment = patch.dict(
            os.environ,
            {
                "HOME": cls.home.name,
                "S_NOTES_DATA_ROOT": cls.home.name,
            },
        )
        cls.environment.start()

    @classmethod
    def tearDownClass(cls) -> None:
        cls.environment.stop()
        cls.home.cleanup()

    def _database(self, name: str):
        temporary = tempfile.TemporaryDirectory(prefix=f"snotes-retention-{name}-")
        self.addCleanup(temporary.cleanup)
        path = Path(temporary.name) / "fixture.db"
        return path, initialize_database(path)

    @staticmethod
    def _insert_logs(connections, count: int, *, prefix: str = "fixture") -> None:
        with connections.write() as connection:
            connection.executemany(
                """
                INSERT INTO ai_call_logs (
                    called_at, actor_target_id, operation_type, request_id,
                    note_id, summary, result_count, handling_mode, status,
                    failure_reason, is_duplicate, entered_confirmation
                ) VALUES (?, 'gale', 'search', ?, NULL, '', 1, 'read',
                          'success', '', 0, 0)
                """,
                (
                    ("2026-10-04T08:00:00+08:00", f"{prefix}-{index:05d}")
                    for index in range(count)
                ),
            )

    @staticmethod
    def _count(connections, table: str) -> int:
        with connections.read() as connection:
            return int(connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0])

    def test_threshold_and_below_do_not_execute_delete(self) -> None:
        for count in (11_999, 12_000):
            with self.subTest(count=count):
                _path, connections = self._database(str(count))
                self._insert_logs(connections, count)
                statements: list[str] = []
                original_open = connections._open

                def traced_open():
                    connection = original_open()
                    connection.set_trace_callback(statements.append)
                    return connection

                with patch.object(connections, "_open", side_effect=traced_open):
                    deleted = SQLiteAiAuditRepository(connections).prune_call_logs()

                self.assertEqual(deleted, 0)
                self.assertEqual(self._count(connections, "ai_call_logs"), count)
                self.assertFalse(
                    any("DELETE FROM AI_CALL_LOGS" in statement.upper() for statement in statements),
                    statements,
                )

    def test_crossing_threshold_keeps_exact_ordered_limit(self) -> None:
        _path, connections = self._database("ordering")
        self._insert_logs(connections, 12_001, prefix="ordered")
        with connections.write() as connection:
            connection.execute(
                "UPDATE ai_call_logs SET called_at = ? WHERE id = 1",
                ("2026-10-05T08:00:00+08:00",),
            )
            connection.execute(
                "UPDATE ai_call_logs SET called_at = ? WHERE id = 12001",
                ("2026-10-03T08:00:00+08:00",),
            )

        deleted = SQLiteAiAuditRepository(connections).prune_call_logs()

        self.assertEqual(deleted, 2_001)
        self.assertEqual(self._count(connections, "ai_call_logs"), 10_000)
        with connections.read() as connection:
            retained = {
                int(row["id"])
                for row in connection.execute("SELECT id FROM ai_call_logs")
            }
        self.assertEqual(retained, {1} | set(range(2_002, 12_001)))

    def test_large_prune_preserves_notes_receipts_and_operation_result(self) -> None:
        _path, connections = self._database("business-data")
        operation_result = {
            "status": "updated",
            "request_id": "operation-kept",
            "note_id": 41,
            "title": "虚构保留笔记",
            "handling_mode": "direct",
            "changed": True,
            "entered_confirmation": False,
            "failure_reason": "",
        }
        with connections.write() as connection:
            connection.execute(
                """
                INSERT INTO notes (
                    id, note_uuid, revision, title, content_markdown,
                    main_category, sub_category, tags, ai_visibility,
                    created_at, updated_at
                ) VALUES (41, 'fictional-note-41', 1, '虚构保留笔记', '- fixture',
                          '项目', '测试', '', 'hidden', ?, ?)
                """,
                ("2026-10-01T08:00:00+08:00", "2026-10-01T08:00:00+08:00"),
            )
            connection.executemany(
                """
                INSERT INTO ai_receipt_inbox (
                    id, raw_text, parse_status, status, source, action,
                    note_id, created_at, updated_at, request_id, actor_target_id,
                    operation_type, result_status
                ) VALUES (?, ?, 'parsed', ?, 'Gale', '新增', ?, ?, ?, ?, 'gale',
                          'create', ?)
                """,
                (
                    (
                        51,
                        "fictional pending",
                        "pending",
                        None,
                        "2026-10-01T08:00:00+08:00",
                        "2026-10-01T08:00:00+08:00",
                        "pending-kept",
                        "pending_confirmation",
                    ),
                    (
                        52,
                        "fictional accepted",
                        "accepted",
                        41,
                        "2026-10-01T09:00:00+08:00",
                        "2026-10-01T09:00:00+08:00",
                        "operation-kept",
                        "updated",
                    ),
                ),
            )
            connection.execute(
                """
                INSERT INTO ai_operations (
                    request_id, actor_target_id, operation_type, status,
                    result_json, created_at, updated_at
                ) VALUES ('operation-kept', 'gale', 'edit', 'updated', ?, ?, ?)
                """,
                (
                    json.dumps(operation_result, ensure_ascii=False),
                    "2026-10-01T09:00:00+08:00",
                    "2026-10-01T09:00:00+08:00",
                ),
            )
            connection.executemany(
                """
                INSERT INTO ai_call_logs (
                    called_at, actor_target_id, operation_type, request_id,
                    note_id, status
                ) VALUES (?, 'gale', ?, ?, ?, 'success')
                """,
                (
                    ("2026-09-01T08:00:00+08:00", "edit", "operation-kept", 41),
                    ("2026-09-01T08:00:01+08:00", "create", "pending-kept", None),
                ),
            )
        with connections.read() as connection:
            notes_before = [
                tuple(row)
                for row in connection.execute("SELECT * FROM notes WHERE id = 41")
            ]
            receipts_before = [
                tuple(row)
                for row in connection.execute(
                    "SELECT * FROM ai_receipt_inbox ORDER BY id"
                )
            ]
            operations_before = [
                tuple(row)
                for row in connection.execute(
                    "SELECT * FROM ai_operations WHERE request_id = 'operation-kept'"
                )
            ]
        self._insert_logs(connections, 15_000, prefix="newer")

        deleted = SQLiteAiAuditRepository(connections).prune_call_logs()

        self.assertEqual(deleted, 5_002)
        self.assertEqual(self._count(connections, "ai_call_logs"), 10_000)
        self.assertEqual(self._count(connections, "notes"), 1)
        self.assertEqual(self._count(connections, "ai_receipt_inbox"), 2)
        with connections.read() as connection:
            notes_after = [
                tuple(row)
                for row in connection.execute("SELECT * FROM notes WHERE id = 41")
            ]
            receipts_after = [
                tuple(row)
                for row in connection.execute(
                    "SELECT * FROM ai_receipt_inbox ORDER BY id"
                )
            ]
            operations_after = [
                tuple(row)
                for row in connection.execute(
                    "SELECT * FROM ai_operations WHERE request_id = 'operation-kept'"
                )
            ]
            receipt_statuses = {
                str(row["status"])
                for row in connection.execute("SELECT status FROM ai_receipt_inbox")
            }
        self.assertEqual(notes_after, notes_before)
        self.assertEqual(receipts_after, receipts_before)
        self.assertEqual(operations_after, operations_before)
        self.assertEqual(receipt_statuses, {"pending", "accepted"})
        operations = SQLiteAiMcpOperationRepository(
            connections,
            SQLiteTagsRepository(connections),
        )
        stored_operation = operations.get_operation("operation-kept", "gale")
        self.assertIsNotNone(stored_operation)
        self.assertEqual(stored_operation["request_id"], "operation-kept")
        self.assertEqual(stored_operation["status"], "updated")
        self.assertEqual(stored_operation["note_id"], 41)
        self.assertTrue(stored_operation["changed"])

    def test_initialize_applies_retention_but_record_never_deletes(self) -> None:
        path, connections = self._database("startup")
        self._insert_logs(connections, 12_001, prefix="startup")

        connections = initialize_database(path)

        self.assertEqual(self._count(connections, "ai_call_logs"), 10_000)
        statements: list[str] = []
        original_open = connections._open

        def traced_open():
            connection = original_open()
            connection.set_trace_callback(statements.append)
            return connection

        event = AiAuditEvent(
            called_at="2026-10-05T08:00:00+08:00",
            actor_target_id="gale",
            operation_type="search",
            request_id="record-only",
            note_id=None,
            summary="",
            result_count=0,
            handling_mode="read",
            status="success",
            failure_reason="",
            is_duplicate=False,
            entered_confirmation=False,
        )
        repository = SQLiteAiAuditRepository(connections)
        with patch.object(connections, "_open", side_effect=traced_open):
            for index in range(3):
                repository.record(replace(event, request_id=f"record-only-{index}"))

        self.assertEqual(self._count(connections, "ai_call_logs"), 10_003)
        self.assertFalse(
            any("DELETE FROM AI_CALL_LOGS" in statement.upper() for statement in statements),
            statements,
        )


if __name__ == "__main__":
    unittest.main()
