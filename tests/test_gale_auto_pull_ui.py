from __future__ import annotations

import os
import sys
import unittest
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QApplication

from src.database import create_gale_submission, initialize_database
from src.main_window import MainWindow, application_settings


class GaleAutoPullUiTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QApplication.instance() or QApplication([])
        initialize_database()

    def setUp(self) -> None:
        self.settings = application_settings()
        self.settings.remove(MainWindow.GALE_AUTO_PULL_SETTING)
        self.settings.sync()
        self.window = MainWindow()
        self.window.show()
        self.app.processEvents()

    def tearDown(self) -> None:
        self.window.close()
        self.app.processEvents()
        self.settings.remove(MainWindow.GALE_AUTO_PULL_SETTING)
        self.settings.sync()

    def open_ai_center(self):
        self.window.open_receipt_inbox()
        self.app.processEvents()
        dialog = self.window.receipt_inbox_dialog
        self.assertIsNotNone(dialog)
        dialog.tabs.setCurrentIndex(3)
        self.app.processEvents()
        return dialog

    def test_auto_receive_defaults_on_and_uses_plain_language(self) -> None:
        dialog = self.open_ai_center()
        self.assertTrue(self.window.gale_auto_pull_enabled)
        self.assertTrue(dialog.auto_pull_checkbox.isChecked())
        self.assertEqual(dialog.auto_pull_checkbox.text(), "自动接收 Gale 投稿")
        labels = [
            child.text()
            for child in dialog.findChildren(type(dialog.auto_pull_status_label))
        ]
        self.assertTrue(
            any("只进入“待确认”" in text for text in labels),
            labels,
        )

    def test_disable_persists_without_starting_a_pull(self) -> None:
        dialog = self.open_ai_center()
        with patch.object(self.window, "start_gale_auto_pull_check") as start:
            dialog.auto_pull_checkbox.setChecked(False)
        start.assert_not_called()
        self.assertFalse(
            self.settings.value(
                MainWindow.GALE_AUTO_PULL_SETTING, True, type=bool
            )
        )

    def test_reenable_requests_an_immediate_check_in_packaged_mode(self) -> None:
        self.window.set_gale_auto_pull_enabled(False)
        dialog = self.open_ai_center()
        with (
            patch.object(sys, "frozen", True, create=True),
            patch("src.main_window.DATA_ROOT_OVERRIDDEN", False),
            patch.object(self.window, "start_gale_auto_pull_check") as start,
        ):
            dialog.auto_pull_checkbox.setChecked(True)
        start.assert_called_once_with()

    def test_packaged_startup_schedules_immediate_nonblocking_check(self) -> None:
        with (
            patch.object(sys, "frozen", True, create=True),
            patch("src.main_window.DATA_ROOT_OVERRIDDEN", False),
            patch.object(QTimer, "singleShot") as single_shot,
        ):
            self.window.configure_gale_auto_pull()
        single_shot.assert_called_once_with(
            0, self.window.start_gale_auto_pull_check
        )

    def test_open_pending_page_and_badge_refresh_after_external_import(self) -> None:
        dialog = self.open_ai_center()
        dialog.tabs.setCurrentIndex(0)
        create_gale_submission(
            external_submission_id="00000000-0000-4000-8000-000000000101",
            idempotency_key="fictional-open-dialog-refresh",
            title="虚构自动刷新投稿",
            body="fictional body",
        )
        with patch("src.main_window.DATA_ROOT_OVERRIDDEN", False):
            self.window.refresh_gale_auto_pull_state(force=True)
        self.app.processEvents()
        self.assertIn("AI中心 (", self.window.receipt_inbox_button.text())
        titles = [card.receipt["title"] for card in dialog.pending_page.cards.values()]
        self.assertIn("虚构自动刷新投稿", titles)


if __name__ == "__main__":
    unittest.main()
