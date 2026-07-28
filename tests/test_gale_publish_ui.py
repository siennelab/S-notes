from __future__ import annotations

import os
import unittest
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication

from src.database import initialize_database
from src.main_window import (
    GalePublishThread,
    MainWindow,
    application_settings,
)


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
                "src.main_window.publish_gale_mirror"
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


if __name__ == "__main__":
    unittest.main()
