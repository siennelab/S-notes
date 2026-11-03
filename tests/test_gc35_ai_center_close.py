"""Real MainWindow regression for closing AI Center during Cloud follow-up."""

from __future__ import annotations

import os
import tempfile
import threading
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("QTWEBENGINE_CHROMIUM_FLAGS", "--disable-gpu")

from PySide6.QtCore import QEventLoop, QTimer
from PySide6.QtTest import QSignalSpy
from PySide6.QtWidgets import QApplication
from shiboken6 import isValid

from src.snotes.bootstrap import build_application
from src.snotes.domain.greencloud import GreenCloudSubmission
from src.snotes.domain.models import NoteLocation
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

    @classmethod
    def tearDownClass(cls) -> None:
        QTimer.singleShot(0, cls.app.quit)
        cls.app.exec()

    def _wait(self, predicate, message: str, timeout: float = 5) -> None:
        if predicate():
            return
        # Use the normal Qt loop, which releases the GIL during native thread
        # cleanup. QTest.qWait can deadlock a PySide worker's final destruction.
        loop = QEventLoop()
        poll = QTimer(loop)
        poll.timeout.connect(lambda: loop.quit() if predicate() else None)
        poll.start(10)
        deadline = QTimer(loop)
        deadline.setSingleShot(True)
        deadline.timeout.connect(loop.quit)
        deadline.start(int(timeout * 1000))
        try:
            loop.exec()
        finally:
            poll.stop()
            deadline.stop()
            poll.timeout.disconnect()
            deadline.timeout.disconnect()
            loop.deleteLater()
        self.assertTrue(predicate(), message)

    def _make_window(self):
        temporary = tempfile.TemporaryDirectory(prefix="snotes-ai-center-close-")
        self.addCleanup(temporary.cleanup)
        _application, window = build_application(
            [],
            data_root=Path(temporary.name) / "fictional-data",
            reminder_authority=LOCAL_REMINDER_AUTHORITY,
        )
        control = SlowGreenCloudControl()
        window.gale_control_service = control

        def cleanup():
            control.release.set()
            self._wait(
                lambda: window.ai_center_dialog is None
                or not window.ai_center_dialog.gale_tasks.has_dialog_tasks,
                "AI Center worker did not finish during cleanup",
            )
            if window.ai_center_dialog is not None:
                window.ai_center_dialog.close()
            window.has_unsaved_changes = False
            window.conflict_active = False
            window.close()
            self._wait(lambda: not window.isVisible(), "Main window did not close")
            window.deleteLater()
            self._wait(lambda: not isValid(window), "Main window was not destroyed")

        self.addCleanup(cleanup)
        notes = []
        for title in ("FICTIONAL A", "FICTIONAL B"):
            note = window.note_service.create_note(NoteLocation())
            notes.append(window.note_service.save_note(
                note.id, title=title, content_markdown=title + " body\n",
                location=note.location, expected_revision=note.revision,
            ))
        window.show()
        self._switch_note(window, notes[0])
        return window, control, notes

    def _switch_note(self, window, note):
        window.switch_note(note.id)
        self._wait(
            lambda: window.current_note is not None
            and window.current_note.id == note.id
            and window.body_edit.can_save
            and window.body_edit.latest_markdown == note.content_markdown,
            "Note navigation did not load the matching body",
        )

    def _accept_greencloud_create(self, window, control):
        receipt, _created = window.receipt_inbox_service.import_greencloud_pending(
            GreenCloudSubmission(
                submission_uuid="00000000-0000-4000-8000-000000000935",
                source_actor="gale", request_id="fictional-ai-center-close-create",
                operation_type="create", note_id=None, expected_version=None,
                payload={"title": "FICTIONAL Gale accepted create",
                         "content": "FICTIONAL accepted body\n", "directory_id": None,
                         "main_category": None, "sub_category": None, "tags": []},
                created_at="2026-10-05T00:00:00Z", delivery_state="delivered",
                delivered_at="2026-10-05T00:01:00Z",
            )
        )
        window.open_ai_center()
        dialog = window.ai_center_dialog
        dialog.receipt_view.accept_receipt(receipt.id)
        self.assertEqual(window.receipt_inbox_service.get_receipt(receipt.id).status,
                         "accepted")
        self._wait(control.started.is_set, "GreenCloud follow-up did not start")
        return dialog

    def test_accept_close_ai_center_keeps_note_navigation_usable(self):
        window, control, notes = self._make_window()
        dialog = self._accept_greencloud_create(window, control)
        dialog.close()
        self.assertFalse(dialog.isVisible())
        self.assertTrue(window.isVisible())
        self.assertFalse(window._close_pending)
        self.assertTrue(dialog.gale_tasks.is_running("greencloud_followup"))
        self._switch_note(window, notes[1])

        control.release.set()
        self._wait(lambda: window.ai_center_dialog is None,
                   "AI Center deferred close did not finish")
        self.assertFalse(dialog.gale_tasks.has_dialog_tasks)
        self.assertFalse(dialog.gale_tasks.is_running("greencloud_followup"))
        self.assertFalse(dialog.gale_tasks.followup_queued)
        self.assertFalse(window._close_pending)
        self._switch_note(window, notes[0])
        window.close()
        self._wait(lambda: not window.isVisible(), "Main window did not close")

    def test_quit_waits_for_followup_once_then_really_closes(self):
        window, control, _notes = self._make_window()
        dialog = self._accept_greencloud_create(window, control)
        close_events = []
        original = type(window).closeEvent

        def record_close(owner, event):
            close_events.append(True)
            original(owner, event)

        with patch.object(type(window), "closeEvent", record_close):
            window.close()  # Same MainWindow close path used by Command+Q.
            self._wait(lambda: window._close_ready, "Editor close barrier did not finish")
            deadline = time.monotonic() + 0.25
            self._wait(lambda: time.monotonic() >= deadline, "GUI loop did not run")
            self.assertTrue(window.isVisible())
            self.assertTrue(dialog.gale_tasks.is_running("greencloud_followup"))
            self.assertLessEqual(len(close_events), 2, "Close is polling the busy worker")
            control.release.set()
            self._wait(lambda: not window.isVisible(), "Worker completion did not close main")
        self.assertFalse(window._close_pending)
        self.assertTrue(not isValid(dialog) or not dialog.isVisible())
        self.assertFalse(dialog.gale_tasks.has_dialog_tasks)
        self.assertFalse(dialog.gale_tasks.is_running("greencloud_followup"))
        self.assertFalse(dialog.gale_tasks.followup_queued)

    def test_quit_drains_queued_followup_before_reporting_idle(self):
        window, control, _notes = self._make_window()
        dialog = self._accept_greencloud_create(window, control)
        idle = QSignalSpy(dialog.backgroundIdle)
        dialog.receipt_view.terminalDecisionSaved.emit()
        self.assertTrue(dialog.gale_tasks.followup_queued)
        window.close()
        self._wait(lambda: window._close_ready, "Editor close barrier did not finish")
        self.assertTrue(window.isVisible())
        self.assertEqual(idle.count(), 0)
        control.release.set()
        self._wait(lambda: not window.isVisible(), "Queued follow-up did not allow close")
        self.assertEqual(control.calls, 2)
        self.assertEqual(idle.count(), 1)
        self.assertFalse(dialog.gale_tasks.has_dialog_tasks)
        self.assertFalse(dialog.gale_tasks.is_running("greencloud_followup"))
        self.assertFalse(dialog.gale_tasks.followup_queued)
        self.assertFalse(window._close_pending)

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
                        or not window.ai_center_dialog.gale_tasks.has_dialog_tasks
                    ),
                    "AI Center worker did not finish during cleanup",
                )
                if window.ai_center_dialog is not None:
                    window.ai_center_dialog.close()
            window.has_unsaved_changes = False
            window.conflict_active = False
            window.close()
            self._wait(lambda: window._close_ready, "Main window did not close")
            window.deleteLater()
            self._wait(lambda: not isValid(window), "Main window was not destroyed")

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
        self.assertTrue(dialog.gale_tasks.has_dialog_tasks)

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
