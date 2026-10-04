"""Real MainWindow regression for closing AI Center during Cloud follow-up."""

from __future__ import annotations

import os
import tempfile
import threading
import time
import unittest
from pathlib import Path
from types import SimpleNamespace

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("QTWEBENGINE_CHROMIUM_FLAGS", "--disable-gpu")

from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication
from shiboken6 import isValid

from src.snotes.bootstrap import build_application
from src.snotes.reminder_authority import LOCAL_REMINDER_AUTHORITY


class SlowGreenCloudControl:
    def __init__(self) -> None:
        self.started = threading.Event()
        self.release = threading.Event()
        self.calls = 0

    def flush_greencloud_decisions(self):
        self.calls += 1
        self.started.set()
        self.release.wait(timeout=5)
        return SimpleNamespace(failed_count=0)

    def last_successful_publish_at(self):
        return ""

    def last_completed_pull_at(self):
        return ""

    def auto_publish_enabled(self):
        return False

    def set_auto_publish_enabled(self, _value):
        return None

    def auto_pull_enabled(self):
        return False

    def set_auto_pull_enabled(self, _value):
        return None

    def manual_update(self):
        return object()

    def check_submissions_now(self):
        return object()


class AiCenterDeferredCloseTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QApplication.instance() or QApplication([])
        cls.app.setQuitOnLastWindowClosed(False)

    def _wait(self, predicate, message: str, timeout: float = 5) -> None:
        deadline = time.monotonic() + timeout
        while not predicate():
            if time.monotonic() >= deadline:
                self.fail(message)
            QTest.qWait(10)

    def test_main_window_survives_ai_center_close_while_followup_runs(self) -> None:
        temporary = tempfile.TemporaryDirectory(prefix="snotes-ai-center-close-")
        self.addCleanup(temporary.cleanup)
        _application, window = build_application(
            [],
            data_root=Path(temporary.name) / "fictional-data",
            reminder_authority=LOCAL_REMINDER_AUTHORITY,
        )
        control = SlowGreenCloudControl()
        window.gale_control_service = control

        def cleanup() -> None:
            control.release.set()
            if window.ai_center_dialog is not None and isValid(window.ai_center_dialog):
                self._wait(
                    lambda: (
                        window.ai_center_dialog is None
                        or not window.ai_center_dialog._gale_threads
                    ),
                    "AI Center worker did not finish during cleanup",
                )
                if window.ai_center_dialog is not None:
                    window.ai_center_dialog.close()
            window.has_unsaved_changes = False
            window.conflict_active = False
            window.close()
            self._wait(lambda: window._close_ready, "Main window did not close")

        self.addCleanup(cleanup)
        window.show()
        window.open_ai_center()
        dialog = window.ai_center_dialog
        self.assertIsNotNone(dialog)
        dialog.receipt_view.terminalDecisionSaved.emit()
        self._wait(control.started.is_set, "Cloud follow-up did not start")

        dialog.close()
        self.app.processEvents()

        self.assertTrue(window.isVisible())
        self.assertTrue(isValid(dialog))
        self.assertFalse(dialog.isVisible())
        self.assertTrue(dialog._gale_threads[0].isRunning())

        control.release.set()
        self._wait(
            lambda: window.ai_center_dialog is None,
            "AI Center was not deleted after its worker finished",
        )
        self.assertTrue(window.isVisible())
        self.assertTrue(isValid(window))
        self.assertEqual(control.calls, 1)


if __name__ == "__main__":
    unittest.main()
