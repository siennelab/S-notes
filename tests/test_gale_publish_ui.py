from __future__ import annotations

import os
import unittest
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication, QToolButton

from src.database import initialize_database
from src.main_window import GalePublishThread, MainWindow


class GalePublishUiTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QApplication.instance() or QApplication([])
        initialize_database()

    def setUp(self) -> None:
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

    def test_existing_action_area_has_minimal_publish_menu(self) -> None:
        button = self.window.gale_menu_button
        self.assertIsInstance(button, QToolButton)
        self.assertEqual(button.toolTip(), "Gale 发布")
        self.assertEqual(button.popupMode(), QToolButton.InstantPopup)
        menu = button.menu()
        self.assertIsNotNone(menu)
        assert menu is not None
        self.assertEqual(
            [action.text() for action in menu.actions()],
            ["立即发布给 Gale", "", "自动发布给 Gale"],
        )
        self.assertTrue(self.window.gale_auto_publish_action.isCheckable())

    def test_manual_publish_starts_background_thread_without_sync_publish(self) -> None:
        with (
            patch(
                "src.main_window.publish_gale_mirror"
            ) as synchronous_publish,
            patch.object(
                GalePublishThread,
                "start",
            ) as start_thread,
        ):
            self.window.gale_publish_action.trigger()

        start_thread.assert_called_once_with()
        synchronous_publish.assert_not_called()
        self.assertEqual(self.window.status_label.text(), "正在发布给 Gale")
        self.window.gale_publish_thread = None


if __name__ == "__main__":
    unittest.main()
