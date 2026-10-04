"""Formal-open pull stays asynchronous and isolated App tests stay offline."""

from __future__ import annotations

import os
from pathlib import Path
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

from PySide6.QtCore import QTimer
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

from src.snotes.bootstrap import build_application
from src.snotes.reminder_authority import LOCAL_REMINDER_AUTHORITY


class _Control:
    def __init__(self):
        self.started = threading.Event()
        self.release = threading.Event()
        self.thread_id = None

    def auto_pull_enabled(self):
        return True

    def check_submissions_now(self):
        self.thread_id = threading.get_ident()
        self.started.set()
        assert self.release.wait(5), "fixture pull was not released"

    def flush_greencloud_decisions(self):
        return self.check_submissions_now()

    def last_successful_publish_at(self):
        return ""

    def last_completed_pull_at(self):
        return ""

    def auto_publish_enabled(self):
        return False

    def set_auto_publish_enabled(self, enabled):
        pass

    def set_auto_pull_enabled(self, enabled):
        pass


class Step11StartupTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.home = tempfile.TemporaryDirectory(prefix="snotes-step11-startup-")
        cls.environment = patch.dict(os.environ, {
            "HOME": cls.home.name, "S_NOTES_DATA_ROOT": cls.home.name,
            "QTWEBENGINE_CHROMIUM_FLAGS": "--disable-gpu",
        })
        cls.environment.start()
        cls.app = QApplication.instance() or QApplication([])
        cls.app.setQuitOnLastWindowClosed(False)

    @classmethod
    def tearDownClass(cls):
        QTimer.singleShot(0, cls.app.quit)
        cls.app.exec()
        cls.environment.stop()
        cls.home.cleanup()

    def _wait(self, predicate):
        deadline = time.monotonic() + 10
        while not predicate():
            if time.monotonic() > deadline:
                self.fail("fixture Qt state timed out")
            QTest.qWait(20)

    def test_isolated_build_never_starts_live_submission_pull(self):
        with tempfile.TemporaryDirectory(prefix="snotes-step11-startup-db-") as name:
            _, window = build_application(data_root=Path(name), reminder_authority=LOCAL_REMINDER_AUTHORITY)
            window.show()
            QTest.qWait(100)
            self.assertFalse(window._automatic_submission_pull)
            self.assertIsNone(window._submission_pull_thread)
            window.close()
            self._wait(lambda: not window.isVisible())

    def test_formal_open_worker_keeps_gui_responsive_and_close_waits_for_pull(self):
        control = _Control()
        with tempfile.TemporaryDirectory(prefix="snotes-step11-startup-db-") as name:
            _, window = build_application(data_root=Path(name), reminder_authority=LOCAL_REMINDER_AUTHORITY)
            window.gale_control_service = control
            window._automatic_submission_pull = True
            window.show()
            self._wait(control.started.is_set)
            self.assertNotEqual(control.thread_id, threading.get_ident())
            tick = []
            QTimer.singleShot(0, lambda: tick.append(True))
            self._wait(lambda: bool(tick))
            window.close()
            QTest.qWait(50)
            self.assertTrue(window.isVisible())
            control.release.set()
            self._wait(lambda: window._submission_pull_thread is None)
            self._wait(lambda: not window.isVisible())

    def test_main_window_close_waits_for_greencloud_receipt_worker(self):
        control = _Control()
        with tempfile.TemporaryDirectory(prefix="snotes-step11-startup-db-") as name:
            _, window = build_application(data_root=Path(name), reminder_authority=LOCAL_REMINDER_AUTHORITY)
            window.gale_control_service = control
            window.show()
            window.open_ai_center()
            window.ai_center_dialog.receipt_view.terminalDecisionSaved.emit()
            self._wait(control.started.is_set)
            window.close()
            QTest.qWait(50)
            self.assertTrue(window.isVisible())
            control.release.set()
            self._wait(lambda: not window.ai_center_dialog._gale_threads)
            window.ai_center_dialog.close()
            self._wait(lambda: not window.isVisible())


if __name__ == "__main__":
    unittest.main()
