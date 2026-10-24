from __future__ import annotations

import json
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from PySide6.QtCore import QTimer
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QHeaderView

from src.snotes.bootstrap import build_application
from src.snotes.reminder_authority import LOCAL_REMINDER_AUTHORITY
from src.snotes.ui.export_dialog import ExportContext, ExportDialog
from src.snotes.ui.floating_combo import FloatingComboBox
from src.snotes.ui.theme import THEMES, build_app_style


class Package4AiSafeExportAuditTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QApplication.instance() or QApplication([])

    def _close_window(self):
        self.window.has_unsaved_changes = False
        self.window.conflict_active = False
        self.window.close()
        deadline = time.monotonic() + 15
        while (not self.window._close_ready or self.window.receipt_refresh_timer.isActive()) and time.monotonic() < deadline:
            QTest.qWait(20)
        self.assertTrue(self.window._close_ready)
        QTest.qWait(20)

    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory(
            prefix="snotes-r2-package4-safe-export-"
        )
        self.addCleanup(self.temporary.cleanup)
        self.data_root = Path(self.temporary.name) / "data"
        _application, self.window = build_application([], data_root=self.data_root, reminder_authority=LOCAL_REMINDER_AUTHORITY)
        self.addCleanup(self._close_window)
        self.rules_path = (
            self.data_root / "config" / "local_redaction_rules.json"
        )
        self.rules_path.parent.mkdir(parents=True, exist_ok=True)
        self.rules_path.write_text(
            json.dumps(
                {
                    "replacements": [
                        {"from": "SecretCanary", "to": "[MASKED]"}
                    ]
                }
            ),
            encoding="utf-8",
        )

    def add_note(
        self,
        title: str,
        *,
        body: str,
        visibility: str,
        targets: tuple[str, ...],
        tags: tuple[str, ...] = (),
    ):
        note = self.window.note_service.create_note()
        note = self.window.note_service.save_note(
            note.id,
            title=title,
            content_markdown=body,
            location=note.location,
            expected_revision=note.revision,
        )
        if tags:
            self.window.tag_service.set_note_tags(note.id, tags)
        self.window.ai_access_metadata_service.set_note_visibility(
            note.id, visibility
        )
        self.window.ai_access_metadata_service.set_note_targets(note.id, targets)
        return self.window.note_service.get_note(note.id)

    def test_markdown_formats_cannot_bypass_safe_export(self):
        bodies = ['Se**cret**Canary', '*Secret*Canary', ':size[SecretCanary]{size="20"}', '[SecretCanary](https://example.com)', '`SecretCanary`']
        for index, body in enumerate(bodies):
            self.add_note(f'format {index}', body=body, visibility='redacted', targets=('gale',))
        plan = self.window.ai_safe_export_service.build_plan('gale')
        self.assertEqual(plan.note_count, 5)
        for note in plan.rendered_notes:
            self.assertTrue(note.markdown.endswith('[MASKED]'))
            self.assertNotIn('SecretCanary', note.markdown)
            self.assertEqual(note.transform_count, 1)
        outcome = self.window.ai_safe_export_service.export('gale', confirm_sensitive=lambda _plan: True)
        self.assertEqual(outcome.status, 'exported')
        files = list(outcome.result.package_dir.rglob('*.md'))
        self.assertEqual(len(files), 5)
        for path in files:
            self.assertTrue(path.read_text(encoding='utf-8').rstrip().endswith('[MASKED]'))

    def test_gale_package_authorizes_materializes_scans_and_does_not_mutate(self) -> None:
        common = self.add_note(
            "Common full",
            body="Common body",
            visibility="full",
            targets=("common",),
        )
        gale = self.add_note(
            "Gale full",
            body="Gale body",
            visibility="full",
            targets=("gale",),
        )
        redacted = self.add_note(
            "SecretCanary title",
            body="secretcanary body",
            visibility="redacted",
            targets=("gale",),
            tags=("SecretCanary-tag",),
        )
        suspicious = self.add_note(
            "Suspicious sample",
            body="Reserved test address 203.0.113.9",
            visibility="full",
            targets=("gale",),
        )
        ke = self.add_note(
            "Ke excluded title",
            body="Ke excluded body 192.0.2.5",
            visibility="full",
            targets=("ke-den",),
        )
        hidden = self.add_note(
            "Hidden excluded title",
            body="Hidden excluded body 198.51.100.99",
            visibility="hidden",
            targets=("gale",),
        )
        note_before = self.window.note_service.get_note(redacted.id)
        metadata_before = self.window.ai_access_metadata_service.get_note_metadata(
            redacted.id
        )

        outcome = self.window.ai_safe_export_service.export(
            "gale", confirm_sensitive=lambda _plan: True
        )

        self.assertEqual(outcome.status, "exported")
        self.assertIsNotNone(outcome.result)
        self.assertEqual(
            {item.note_id for item in outcome.plan.rendered_notes},
            {common.id, gale.id, redacted.id, suspicious.id},
        )
        self.assertTrue(outcome.plan.has_sensitive_findings)
        manifest_text = outcome.result.manifest_path.read_text(encoding="utf-8")
        manifest = json.loads(manifest_text)
        self.assertEqual(manifest["package_version"], "s-notes-ai-safe-r1")
        self.assertEqual(manifest["target"], "Gale")
        self.assertEqual(manifest["target_includes"], ["通用", "Gale"])
        self.assertTrue(manifest["has_suspicious_sensitive_content"])
        self.assertEqual(manifest["sensitive_scan"]["ip_address"]["count"], 1)
        self.assertEqual(manifest["note_count"], 4)
        package_text = manifest_text + "\n" + "\n".join(
            path.read_text(encoding="utf-8")
            for path in (outcome.result.package_dir / "notes").glob("*.md")
        )
        self.assertIn("[MASKED]", package_text)
        for excluded in (
            "SecretCanary",
            ke.title,
            "Ke excluded body",
            hidden.title,
            "Hidden excluded body",
        ):
            self.assertNotIn(excluded, package_text)
        self.assertEqual(
            self.window.note_service.get_note(redacted.id), note_before
        )
        self.assertEqual(
            self.window.ai_access_metadata_service.get_note_metadata(redacted.id),
            metadata_before,
        )

        logs = self.window.ai_audit_service.list_call_logs("export")
        self.assertEqual(len(logs), 1)
        self.assertEqual(logs[0].actor_target_id, "gale")
        self.assertEqual(logs[0].status, "success")
        self.assertEqual(logs[0].result_count, 4)
        serialized_logs = json.dumps(
            [row.__dict__ if hasattr(row, "__dict__") else str(row) for row in logs],
            ensure_ascii=False,
        )
        self.assertNotIn("Common body", serialized_logs)
        self.assertNotIn("SecretCanary", serialized_logs)

    def test_common_target_exports_only_common(self) -> None:
        common = self.add_note(
            "Common only",
            body="Common safe",
            visibility="full",
            targets=("common",),
        )
        self.add_note(
            "Gale only",
            body="Gale private",
            visibility="full",
            targets=("gale",),
        )
        outcome = self.window.ai_safe_export_service.export("common")
        self.assertEqual(
            [item.note_id for item in outcome.plan.rendered_notes],
            [common.id],
        )
        manifest = json.loads(
            outcome.result.manifest_path.read_text(encoding="utf-8")
        )
        self.assertEqual(manifest["target_includes"], ["通用"])

    def test_broken_rule_cases_exclude_transformed_note_but_keep_full(self) -> None:
        full = self.add_note(
            "Full remains",
            body="Full body",
            visibility="full",
            targets=("gale",),
        )
        transformed = self.add_note(
            "SecretCanary blocked",
            body="SecretCanary raw",
            visibility="redacted",
            targets=("gale",),
        )
        cases = {
            "missing": None,
            "bad-json": "{ bad",
            "empty": json.dumps({"replacements": []}),
        }
        for name, content in cases.items():
            with self.subTest(name=name):
                if content is None:
                    self.rules_path.unlink(missing_ok=True)
                else:
                    self.rules_path.write_text(content, encoding="utf-8")
                plan = self.window.ai_safe_export_service.build_plan("gale")
                self.assertEqual(
                    [item.note_id for item in plan.rendered_notes], [full.id]
                )
                self.assertTrue(plan.warnings)
                serialized = json.dumps(
                    {
                        "warnings": plan.warnings,
                        "notes": [item.markdown for item in plan.rendered_notes],
                    },
                    ensure_ascii=False,
                )
                self.assertNotIn(transformed.title, serialized)

    def test_fail_closed_no_data_and_unknown_actor_are_audited_privately(self) -> None:
        self.add_note(
            "SecretCanary only",
            body="SecretCanary raw body",
            visibility="redacted",
            targets=("gale",),
        )
        self.rules_path.unlink()
        outcome = self.window.ai_safe_export_service.export("gale")
        self.assertEqual(outcome.status, "no_data")
        self.assertEqual(list((self.data_root / "exports" / "ai_readbox").glob("*")), [])
        log = self.window.ai_audit_service.list_call_logs("export")[0]
        self.assertEqual((log.status, log.result_count), ("success", 0))
        self.assertEqual(log.failure_reason, "redaction_unavailable")

        with self.assertRaisesRegex(ValueError, "未知 AI 导出对象"):
            self.window.ai_safe_export_service.export("unknown")
        failed = self.window.ai_audit_service.list_call_logs("export")[0]
        self.assertEqual(failed.status, "failed")
        self.assertEqual(failed.failure_reason, "unknown_actor")
        self.assertNotIn("SecretCanary", str(self.window.ai_audit_service.list_call_logs()))

    def test_sensitive_cancel_writes_no_package_and_records_confirmation(self) -> None:
        self.add_note(
            "Sensitive cancellation",
            body="Reserved 198.51.100.7",
            visibility="full",
            targets=("gale",),
        )
        export_root = self.data_root / "exports" / "ai_readbox"
        outcome = self.window.ai_safe_export_service.export(
            "gale", confirm_sensitive=lambda _plan: False
        )
        self.assertEqual(outcome.status, "cancelled")
        self.assertFalse(export_root.exists())
        log = self.window.ai_audit_service.list_call_logs("export")[0]
        self.assertEqual(log.status, "cancelled")
        self.assertTrue(log.entered_confirmation)
        self.assertEqual(log.failure_reason, "sensitive_confirmation_cancelled")

    def test_audit_order_filter_and_summary_privacy(self) -> None:
        self.window.ai_audit_service.record_event(
            "gale",
            "search",
            request_id="first",
            status="success",
        )
        self.window.ai_audit_service.record_event(
            "ke-den",
            "get_note",
            request_id="second",
            status="success",
        )
        rows = self.window.ai_audit_service.list_call_logs()
        self.assertEqual([row.request_id for row in rows], ["second", "first"])
        self.assertEqual(
            [row.request_id for row in self.window.ai_audit_service.list_call_logs("search")],
            ["first"],
        )
        self.assertEqual(rows[0].actor_display, "Ke")
        self.assertFalse(hasattr(rows[0], "summary"))

    def test_export_dialog_registry_and_ai_center_log_ui(self) -> None:
        note = self.add_note(
            "UI export",
            body="Safe UI body",
            visibility="full",
            targets=("gale",),
        )
        context = ExportContext(note.id, note.location, "directory", (), (), ())
        dialog = ExportDialog(
            self.window.export_service,
            context,
            ai_safe_export_service=self.window.ai_safe_export_service,
        )
        self.addCleanup(dialog.close)
        self.assertEqual(
            tuple(
                (
                    dialog.ai_target_combo.itemText(index),
                    dialog.ai_target_combo.itemData(index),
                )
                for index in range(dialog.ai_target_combo.count())
            ),
            (
                ("通用", "common"),
                ("Gale", "gale"),
                ("砚", "yan"),
                ("Clawbot", "clawbot"),
                ("Antigravity", "antigravity"),
                ("Claude", "claude-code"),
                ("Ke", "ke-den"),
            ),
        )
        dialog.ai_target_combo.setCurrentIndex(
            dialog.ai_target_combo.findData("gale")
        )
        with patch(
            "src.snotes.ui.export_dialog.QMessageBox.information"
        ) as information:
            dialog._export_ai_safe()
        information.assert_called_once()
        self.window.ai_audit_service.record_event(
            "gale",
            "export",
            request_id="ui-cancel-1",
            status="cancelled",
            failure_reason="sensitive_confirmation_cancelled",
            entered_confirmation=True,
        )
        self.window.ai_audit_service.record_event(
            "gale",
            "search",
            request_id="search-ui",
            status="success",
        )

        self.window.open_ai_center()
        self.app.processEvents()
        center = self.window.ai_center_dialog
        self.assertEqual(
            [center.tabs.tabText(index) for index in range(center.tabs.count())],
            ["待确认 / 已处理", "笔记调用记录", "提醒操作记录"],
        )
        center.tabs.setCurrentWidget(center.log_page)
        self.app.processEvents()
        self.assertIsInstance(center.log_filter, FloatingComboBox)
        self.assertEqual(center.log_filter.objectName(), "aiCallLogFilter")
        self.assertEqual(center.log_filter.itemText(1), "导出")
        self.assertEqual(center.log_filter.itemData(1), "export")
        self.assertEqual(center.log_table.rowCount(), 3)
        displayed = " ".join(
            center.log_table.item(row, column).text()
            for row in range(center.log_table.rowCount())
            for column in range(center.log_table.columnCount())
        )
        self.assertIn("Gale", displayed)
        self.assertIn("导出", displayed)
        self.assertIn("搜索", displayed)
        self.assertIn("读取", displayed)
        self.assertIn("成功", displayed)
        self.assertIn("已取消", displayed)
        self.assertIn("人工确认已取消", displayed)
        self.assertNotIn("export", displayed)
        self.assertNotIn("read", displayed)
        self.assertNotIn("success", displayed)
        self.assertNotIn("cancelled", displayed)
        self.assertNotIn("Safe UI body", displayed)

        export_index = center.log_filter.findData("export")
        center.log_filter.setCurrentIndex(export_index)
        self.app.processEvents()
        self.assertEqual(center.log_filter.currentData(), "export")
        self.assertEqual(center.log_table.rowCount(), 2)
        filtered_request_ids = {
            center.log_table.item(row, 3).text()
            for row in range(center.log_table.rowCount())
        }
        self.assertEqual(len(filtered_request_ids), 2)
        self.assertIn("ui-cancel-1", filtered_request_ids)
        self.assertNotIn("search-ui", filtered_request_ids)

    def test_export_and_call_log_controls_are_themed_in_all_three_modes(self) -> None:
        for theme_name in THEMES:
            with self.subTest(theme=theme_name):
                style = build_app_style(theme_name)
                self.assertIn("#aiSafeExportSection", style)
                self.assertIn("#aiCallLogTable", style)
                self.assertIn("#aiCallLogFilter", style)
                self.assertIn("#aiCenterTabs QTabBar::tab:selected", style)

    def test_large_call_log_switches_repeatedly_without_blocking_events(self) -> None:
        for index in range(625):
            self.window.ai_audit_service.record_event(
                "clawbot",
                "search",
                request_id=f"fictional-call-log-{index:04d}",
                status="success",
            )
        self.window.open_ai_center()
        self.app.processEvents()
        center = self.window.ai_center_dialog
        header = center.log_table.horizontalHeader()
        self.assertTrue(
            all(
                header.sectionResizeMode(column) != QHeaderView.ResizeToContents
                for column in range(center.log_table.columnCount())
            )
        )

        heartbeat = []
        QTimer.singleShot(0, lambda: heartbeat.append(True))
        for _attempt in range(4):
            center.tabs.setCurrentWidget(center.log_page)
            self.app.processEvents()
            self.assertEqual(center.log_table.rowCount(), 100)
            center.tabs.setCurrentWidget(center.receipt_view)
            self.app.processEvents()
        QTest.qWait(20)

        self.assertEqual(heartbeat, [True])
        self.assertTrue(center.isVisible())
        self.assertTrue(center.isEnabled())


if __name__ == "__main__":
    unittest.main()
