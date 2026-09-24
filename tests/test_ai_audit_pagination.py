"""AI call-log pagination and lazy UI loading over fictional isolated data."""

from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from PySide6.QtCore import QTimer
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

from src.snotes.application.ai_access_metadata_service import AiAccessMetadataService
from src.snotes.application.ai_audit_service import AiAuditService
from src.snotes.application.ai_receipt_inbox_service import AiReceiptInboxService
from src.snotes.application.note_service import NoteService
from src.snotes.application.tag_service import TagService
from src.snotes.storage.repositories.ai_audit import SQLiteAiAuditRepository
from src.snotes.storage.repositories.ai_mcp_operations import (
    SQLiteAiMcpOperationRepository,
)
from src.snotes.storage.repositories.ai_receipts import SQLiteAiReceiptInboxRepository
from src.snotes.storage.repositories.directories import SQLiteDirectoriesRepository
from src.snotes.storage.repositories.notes import SQLiteNotesRepository
from src.snotes.storage.repositories.tags import SQLiteTagsRepository
from src.snotes.storage.schema import initialize_database
from src.snotes.ui.ai_center_dialog import AiCenterDialog


class AiAuditPaginationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.home = tempfile.TemporaryDirectory(prefix="snotes-audit-page-home-")
        cls.environment = patch.dict(
            os.environ,
            {
                "HOME": cls.home.name,
                "S_NOTES_DATA_ROOT": cls.home.name,
                "QT_QPA_PLATFORM": "offscreen",
                "QTWEBENGINE_CHROMIUM_FLAGS": "--disable-gpu",
                "PATH": "/usr/bin:/bin",
            },
        )
        cls.environment.start()
        cls.app = QApplication.instance() or QApplication([])
        cls.app.setQuitOnLastWindowClosed(False)

    @classmethod
    def tearDownClass(cls) -> None:
        QTimer.singleShot(0, cls.app.quit)
        cls.app.exec()
        cls.environment.stop()
        cls.home.cleanup()

    def setUp(self) -> None:
        temporary = tempfile.TemporaryDirectory(prefix="snotes-audit-page-db-")
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.connections = initialize_database(self.root / "fixture.db")
        directories = SQLiteDirectoriesRepository(self.connections)
        tags = SQLiteTagsRepository(self.connections)
        notes_repository = SQLiteNotesRepository(self.connections, directories)
        self.metadata = AiAccessMetadataService(notes_repository)
        self.receipts = AiReceiptInboxService(
            SQLiteAiReceiptInboxRepository(self.connections, directories, tags),
            notes=NoteService(notes_repository),
            tags=TagService(tags),
            ai_metadata=self.metadata,
            mcp_operations=SQLiteAiMcpOperationRepository(self.connections, tags),
        )
        self.audit = AiAuditService(SQLiteAiAuditRepository(self.connections))

    def _insert_logs(
        self,
        count: int,
        *,
        operation_type: str = "search",
        called_at: str = "2026-09-24T08:00:00+08:00",
        prefix: str = "fictional-page",
    ) -> None:
        with self.connections.write() as connection:
            connection.executemany(
                """
                INSERT INTO ai_call_logs (
                    called_at, actor_target_id, operation_type, request_id,
                    note_id, summary, result_count, handling_mode, status,
                    failure_reason, is_duplicate, entered_confirmation
                ) VALUES (?, 'gale', ?, ?, NULL, '', 1, 'read', 'success', '', 0, 0)
                """,
                (
                    (called_at, operation_type, f"{prefix}-{index:05d}")
                    for index in range(count)
                ),
            )

    def _insert_operation(
        self,
        request_id: str,
        actor_target_id: str,
        *,
        column_status: str,
        result_status: str,
    ) -> None:
        with self.connections.write() as connection:
            connection.execute(
                """
                INSERT INTO ai_operations (
                    request_id, actor_target_id, operation_type, status,
                    result_json, created_at, updated_at
                ) VALUES (?, ?, 'edit', ?, ?, ?, ?)
                """,
                (
                    request_id,
                    actor_target_id,
                    column_status,
                    json.dumps({"status": result_status}, ensure_ascii=False),
                    "2026-09-24T08:00:00+08:00",
                    "2026-09-24T08:00:00+08:00",
                ),
            )

    def _dialog(self) -> AiCenterDialog:
        dialog = AiCenterDialog(
            self.receipts,
            self.metadata,
            audit=self.audit,
        )
        self.addCleanup(dialog.close)
        return dialog

    @staticmethod
    def _request_ids(dialog: AiCenterDialog) -> list[str]:
        return [
            dialog.log_table.item(row, 3).text()
            for row in range(dialog.log_table.rowCount())
        ]

    def test_twenty_thousand_rows_use_one_bounded_join_query_per_page(self) -> None:
        self._insert_logs(20_000)
        for index in range(150):
            self._insert_operation(
                f"fictional-page-{19_999 - index:05d}",
                "gale",
                column_status="accepted",
                result_status="conflict" if index == 0 else "accepted",
            )

        statements: list[str] = []
        open_count = 0
        original_open = self.connections._open

        def traced_open():
            nonlocal open_count
            open_count += 1
            connection = original_open()
            connection.set_trace_callback(statements.append)
            return connection

        with patch.object(self.connections, "_open", side_effect=traced_open):
            first = self.audit.list_call_logs_page(offset=0)
            middle = self.audit.list_call_logs_page(offset=10_000, limit=100)

        self.assertEqual(len(first.rows), 100)
        self.assertTrue(first.has_more)
        self.assertEqual(first.rows[0].request_id, "fictional-page-19999")
        self.assertEqual(first.rows[0].operation_status, "conflict")
        self.assertEqual(len(middle.rows), 100)
        self.assertTrue(middle.has_more)
        self.assertEqual(middle.rows[0].request_id, "fictional-page-09999")
        self.assertEqual(open_count, 2)
        page_selects = [
            statement
            for statement in statements
            if "FROM ai_call_logs" in statement
        ]
        self.assertEqual(len(page_selects), 2, page_selects)
        for statement in page_selects:
            normalized = " ".join(statement.upper().split())
            self.assertIn("LEFT JOIN AI_OPERATIONS", normalized)
            self.assertIn("ORDER BY", normalized)
            self.assertIn("CALLED_AT DESC", normalized)
            self.assertIn("ID DESC", normalized)
            self.assertIn("LIMIT 101", normalized)

    def test_custom_page_size_and_invalid_bounds(self) -> None:
        self._insert_logs(4)
        page = self.audit.list_call_logs_page(offset=1, limit=2)
        self.assertEqual(
            [row.request_id for row in page.rows],
            ["fictional-page-00002", "fictional-page-00001"],
        )
        self.assertTrue(page.has_more)
        with self.assertRaises(ValueError):
            self.audit.list_call_logs_page(limit=0)
        with self.assertRaises(ValueError):
            self.audit.list_call_logs_page(offset=-1)

    def test_join_requires_nonempty_request_and_matching_actor_and_uses_json_status(self) -> None:
        fixtures = (
            ("json-wins", "gale", "accepted", "conflict"),
            ("column-loses", "gale", "conflict", "accepted"),
            ("actor-mismatch", "ke-den", "conflict", "conflict"),
            ("", "gale", "conflict", "conflict"),
        )
        with self.connections.write() as connection:
            connection.executemany(
                """
                INSERT INTO ai_call_logs (
                    called_at, actor_target_id, operation_type, request_id,
                    note_id, summary, result_count, handling_mode, status,
                    failure_reason, is_duplicate, entered_confirmation
                ) VALUES (?, 'gale', 'edit', ?, NULL, '', 0, 'direct', ?, ?, ?, 0)
                """,
                (
                    (
                        f"2026-09-24T08:00:0{index}+08:00",
                        request_id,
                        "failed" if request_id == "json-wins" else "updated",
                        "unknown_actor" if request_id == "json-wins" else "",
                        1 if request_id == "json-wins" else 0,
                    )
                    for index, (request_id, *_rest) in enumerate(fixtures)
                ),
            )
        for request_id, actor, column_status, result_status in fixtures:
            self._insert_operation(
                request_id,
                actor,
                column_status=column_status,
                result_status=result_status,
            )

        rows = {
            row.request_id: row
            for row in self.audit.list_call_logs_page(limit=100).rows
            if row.request_id
        }
        self.assertEqual(rows["json-wins"].operation_status, "conflict")
        self.assertEqual(rows["column-loses"].operation_status, "accepted")
        self.assertIsNone(rows["actor-mismatch"].operation_status)
        empty = next(
            row
            for row in self.audit.list_call_logs_page(limit=100).rows
            if not row.request_id
        )
        self.assertIsNone(empty.operation_status)

        with patch.object(
            self.receipts,
            "mcp_get_operation",
            side_effect=AssertionError("log rendering must not issue per-row queries"),
        ):
            dialog = self._dialog()
            dialog.tabs.setCurrentWidget(dialog.log_page)
            self.app.processEvents()
        request_index = {
            dialog.log_table.item(index, 3).text(): index
            for index in range(dialog.log_table.rowCount())
        }
        conflict_index = request_index["json-wins"]
        self.assertEqual(
            dialog.log_table.item(conflict_index, 6).text(),
            "版本冲突 · 未写入",
        )
        conflict_status = dialog.log_table.item(conflict_index, 7).text()
        self.assertIn("失败", conflict_status)
        self.assertIn("未知 AI 对象", conflict_status)
        self.assertIn("重复请求", conflict_status)
        self.assertEqual(
            dialog.log_table.item(request_index["column-loses"], 6).text(),
            "直接执行",
        )
        self.assertEqual(
            dialog.log_table.item(request_index["actor-mismatch"], 6).text(),
            "直接执行",
        )

    def test_dialog_is_lazy_and_switching_back_does_not_reload(self) -> None:
        self._insert_logs(20_000)
        with patch.object(
            self.audit,
            "list_call_logs_page",
            wraps=self.audit.list_call_logs_page,
        ) as list_page:
            dialog = self._dialog()
            self.assertEqual(list_page.call_count, 0)
            self.assertFalse(dialog._logs_loaded)

            dialog.tabs.setCurrentWidget(dialog.log_page)
            self.app.processEvents()
            self.assertEqual(list_page.call_count, 1)
            self.assertTrue(dialog._logs_loaded)
            self.assertEqual(dialog.log_table.rowCount(), 100)
            self.assertEqual(dialog.log_offset, 0)

            dialog.tabs.setCurrentWidget(dialog.receipt_view)
            dialog.tabs.setCurrentWidget(dialog.log_page)
            self.app.processEvents()
            self.assertEqual(list_page.call_count, 1)

        self.assertEqual(dialog.log_page_size, 100)
        self.assertIsNotNone(dialog.log_previous_button)
        self.assertIsNotNone(dialog.log_next_button)
        self.assertIsNotNone(dialog.log_page_label)
        self.assertIsNotNone(dialog.log_refresh_button)

    def test_previous_next_order_last_page_and_filter_refresh_reset(self) -> None:
        self._insert_logs(205, operation_type="search", prefix="stable-search")
        self._insert_logs(3, operation_type="export", prefix="stable-export")
        dialog = self._dialog()
        dialog.tabs.setCurrentWidget(dialog.log_page)
        self.app.processEvents()

        self.assertEqual(self._request_ids(dialog)[:3], [
            "stable-export-00002",
            "stable-export-00001",
            "stable-export-00000",
        ])
        self.assertEqual(self._request_ids(dialog)[3], "stable-search-00204")
        self.assertFalse(dialog.log_previous_button.isEnabled())
        self.assertTrue(dialog.log_next_button.isEnabled())

        dialog.next_log_page()
        self.assertEqual(dialog.log_offset, 100)
        self.assertEqual(self._request_ids(dialog)[0], "stable-search-00107")
        second_page = self._request_ids(dialog)
        dialog.previous_log_page()
        dialog.next_log_page()
        self.assertEqual(self._request_ids(dialog), second_page)

        dialog.next_log_page()
        self.assertEqual(dialog.log_offset, 200)
        self.assertEqual(self._request_ids(dialog), [
            f"stable-search-{index:05d}" for index in range(7, -1, -1)
        ])
        self.assertEqual(dialog.log_page_label.text(), "第 3 页 · 8 条")
        self.assertTrue(dialog.log_previous_button.isEnabled())
        self.assertFalse(dialog.log_next_button.isEnabled())

        export_index = dialog.log_filter.findData("export")
        dialog.log_filter.setCurrentIndex(export_index)
        self.app.processEvents()
        self.assertEqual(dialog.log_offset, 0)
        self.assertEqual(self._request_ids(dialog), [
            "stable-export-00002",
            "stable-export-00001",
            "stable-export-00000",
        ])
        self.assertFalse(dialog.log_previous_button.isEnabled())
        self.assertFalse(dialog.log_next_button.isEnabled())

        dialog.log_filter.setCurrentIndex(dialog.log_filter.findData("create"))
        self.app.processEvents()
        self.assertEqual(dialog.log_offset, 0)
        self.assertEqual(dialog.log_table.rowCount(), 0)
        self.assertEqual(dialog.log_page_label.text(), "暂无调用记录")
        self.assertFalse(dialog.log_previous_button.isEnabled())
        self.assertFalse(dialog.log_next_button.isEnabled())

        dialog.log_filter.setCurrentIndex(dialog.log_filter.findData("search"))
        dialog.next_log_page()
        self.assertEqual(dialog.log_offset, 100)
        dialog.log_refresh_button.click()
        self.app.processEvents()
        self.assertEqual(dialog.log_offset, 0)
        self.assertEqual(self._request_ids(dialog)[0], "stable-search-00204")
        self.assertEqual(dialog.log_page_label.text(), "第 1 页 · 100 条")


if __name__ == "__main__":
    unittest.main()
