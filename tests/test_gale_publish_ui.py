from __future__ import annotations

import os
import unittest
from unittest.mock import MagicMock, patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication

from src.database import initialize_database
from src.main_window import (
    GalePublishThread,
    MainWindow,
    application_settings,
)
from src.services.gale_publish_service import GalePublishResult


class GalePublishUiTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QApplication.instance() or QApplication([])
        initialize_database()

    def setUp(self) -> None:
        self.settings = application_settings()
        self.settings.remove(MainWindow.GALE_AUTO_PUBLISH_SETTING)
        self.settings.sync()
        self.window = MainWindow()
        self.window.show()
        self.app.processEvents()

    def tearDown(self) -> None:
        thread = self.window.gale_publish_thread
        if thread is not None and thread.isRunning():
            thread.wait(5000)
        self.window.gale_publish_thread = None
        self.window.close()
        self.app.processEvents()
        self.settings.remove(MainWindow.GALE_AUTO_PUBLISH_SETTING)
        self.settings.sync()

    def open_ai_center(self):
        self.window.open_receipt_inbox()
        self.app.processEvents()
        dialog = self.window.receipt_inbox_dialog
        self.assertIsNotNone(dialog)
        return dialog

    def test_publish_menu_is_removed_from_editor_and_controls_are_in_settings(self) -> None:
        self.assertFalse(hasattr(self.window, "gale_menu_button"))
        dialog = self.open_ai_center()
        self.assertEqual(dialog.tabs.count(), 4)
        self.assertEqual(dialog.tabs.tabText(3), "设置")
        self.assertFalse(dialog.remote_auto_update.isChecked())
        self.assertEqual(dialog.remote_auto_update.text(), "自动更新")
        self.assertEqual(dialog.remote_update_now.text(), "立即更新")

    def test_auto_publish_default_does_not_start_monitoring(self) -> None:
        self.assertFalse(self.window.gale_auto_scheduler.enabled)
        self.assertFalse(self.window.gale_watch_timer.isActive())

    def test_enabling_auto_publish_starts_monitoring_only_once(self) -> None:
        with (
            patch.object(self.window.gale_watch_timer, "start") as start_timer,
            patch.object(
                self.window,
                "check_gale_auto_publish",
            ) as check_publish,
        ):
            checkbox = self.open_ai_center().remote_auto_update
            checkbox.setChecked(True)
            checkbox.setChecked(True)

        start_timer.assert_called_once_with()
        check_publish.assert_called_once_with()
        self.assertTrue(self.window.gale_auto_scheduler.enabled)

    def test_disabling_auto_publish_prevents_new_publish_tasks(self) -> None:
        checkbox = self.open_ai_center().remote_auto_update
        checkbox.setChecked(True)
        checkbox.setChecked(False)

        with patch.object(self.window, "start_gale_publish") as start_publish:
            self.window.check_gale_auto_publish(network_restored=True)

        start_publish.assert_not_called()
        self.assertFalse(self.window.gale_auto_scheduler.enabled)
        self.assertFalse(self.window.gale_watch_timer.isActive())

    def test_manual_publish_starts_background_thread_without_sync_publish(self) -> None:
        dialog = self.open_ai_center()
        self.assertFalse(dialog.remote_auto_update.isChecked())
        with (
            patch(
                "src.main_window.generate_validate_and_publish_gale_mirror"
            ) as synchronous_publish,
            patch.object(
                GalePublishThread,
                "start",
            ) as start_thread,
        ):
            dialog.remote_update_now.click()

        start_thread.assert_called_once_with()
        synchronous_publish.assert_not_called()
        self.assertEqual(self.window.status_label.text(), "正在发布给 Gale")
        self.assertFalse(dialog.remote_update_now.isEnabled())
        self.window.gale_publish_thread = None

    def test_manual_and_auto_publish_use_same_generation_thread_inputs(self) -> None:
        with patch.object(GalePublishThread, "start"):
            self.window.start_gale_publish(manual=True)
            manual_thread = self.window.gale_publish_thread
            self.assertIsNotNone(manual_thread)
            self.window.gale_publish_thread = None
            self.window.gale_auto_scheduler.running = False

            self.window.start_gale_publish(manual=False)
            auto_thread = self.window.gale_publish_thread

        self.assertIsNotNone(auto_thread)
        self.assertTrue(manual_thread.manual)
        self.assertFalse(auto_thread.manual)
        self.assertEqual(manual_thread.config, auto_thread.config)
        self.assertEqual(
            manual_thread.source_database,
            auto_thread.source_database,
        )
        self.assertEqual(
            manual_thread.redaction_rules_path,
            auto_thread.redaction_rules_path,
        )
        manual_thread.deleteLater()
        auto_thread.deleteLater()
        self.window.gale_publish_thread = None

    def test_auto_publish_observes_source_when_local_mirror_is_missing(self) -> None:
        self.window.gale_auto_scheduler.enable()
        digest = "a" * 64
        with (
            patch(
                "src.main_window.source_inputs_sha256",
                return_value=digest,
            ) as source_hash,
            patch.object(self.window, "start_gale_publish") as start_publish,
        ):
            self.window.check_gale_auto_publish(network_restored=True)

        source_hash.assert_called_once()
        self.assertEqual(self.window.gale_source_signature, digest)
        start_publish.assert_called_once_with(manual=False)

    def test_auto_validation_failure_reports_check_without_modal_storm(self) -> None:
        thread = MagicMock()
        thread.manual = False
        thread.result = GalePublishResult(
            status="failed",
            sha256="",
            note_count=0,
            reason="local_validation_failed",
            previous_preserved=True,
            check="fts_content_mismatch",
        )
        self.window.gale_publish_thread = thread

        with patch("src.main_window.QMessageBox.warning") as warning:
            self.window.handle_gale_publish_finished()

        warning.assert_not_called()
        message = self.window.status_label.text()
        self.assertIn("本地安全镜像验证未通过", message)
        self.assertIn("FTS_CONTENT_MISMATCH", message)
        self.assertIn("已继续使用上一版", message)
        self.assertIsNone(self.window.gale_publish_thread)

    def test_manual_validation_failure_reports_check_in_one_dialog(self) -> None:
        thread = MagicMock()
        thread.manual = True
        thread.result = GalePublishResult(
            status="failed",
            sha256="",
            note_count=0,
            reason="local_validation_failed",
            previous_preserved=True,
            check="fts_content_mismatch",
        )
        self.window.gale_publish_thread = thread

        with patch("src.main_window.QMessageBox.warning") as warning:
            self.window.handle_gale_publish_finished()

        warning.assert_called_once()
        self.assertIn("FTS_CONTENT_MISMATCH", warning.call_args.args[2])


if __name__ == "__main__":
    unittest.main()
