"""Regressions for Remote Reminder QThread ownership during application close."""

from __future__ import annotations

import os
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("QTWEBENGINE_CHROMIUM_FLAGS", "--disable-gpu")

from PySide6.QtCore import QEventLoop, QThread, QTimer
from PySide6.QtTest import QSignalSpy
from PySide6.QtWidgets import QApplication
from shiboken6 import delete, isValid

from src.snotes.bootstrap import build_application
from src.snotes.domain.reminder_remote_contract import REMINDER_WIRE_SCHEMA_VERSION
from src.snotes.reminder_authority import LOCAL_REMINDER_AUTHORITY
from src.snotes.ui.remote_reminder_controller import RemoteReminderController


def _snapshot() -> dict:
    return {
        "schema_version": REMINDER_WIRE_SCHEMA_VERSION,
        "snapshot_time": "2099-01-01T00:00:00Z",
        "labels": [],
        "tables": [
            {
                "table_id": 1,
                "title": "FICTIONAL lifecycle table",
                "label": {"label_id": 1, "name": "FICTIONAL"},
            }
        ],
        "items": [],
        "settings": {},
        "badge_count": 0,
    }


class _ImmediateClient:
    def __init__(self) -> None:
        self.close_calls = 0

    def snapshot(self) -> dict:
        return _snapshot()

    def command(self, _name: str, _payload: dict) -> dict:
        return {"status": "ok"}

    def close(self) -> None:
        self.close_calls += 1


class _BlockedClient(_ImmediateClient):
    def __init__(self) -> None:
        super().__init__()
        self.started = threading.Event()
        self.release = threading.Event()

    def snapshot(self) -> dict:
        self.started.set()
        if not self.release.wait(timeout=5):
            raise TimeoutError("test did not release Remote Reminder worker")
        return _snapshot()


class _RecordingThread(QThread):
    """Record whether native deletion was requested before owner cleanup."""

    observations: list[tuple[bool, bool]] = []
    instances: list["_RecordingThread"] = []

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.owner = parent
        self.instances.append(self)

    def deleteLater(self) -> None:
        self.observations.append(
            (self in self.owner._threads, self in self.owner._workers)
        )
        # Hold the native object until every finished callback has run. This
        # makes the old connection order observable without relying on the
        # timing of Qt's DeferredDelete delivery.


class RemoteReminderLifecycleTests(unittest.TestCase):
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
        # A native Qt event loop releases the GIL while QThread finishes and
        # DeferredDelete events run. QTest.qWait can deadlock this boundary.
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
            poll.deleteLater()
            deadline.deleteLater()
            loop.deleteLater()
        self.assertTrue(predicate(), message)

    def _make_window(self, controller: RemoteReminderController):
        temporary = tempfile.TemporaryDirectory(
            prefix="snotes-remote-reminder-lifecycle-"
        )
        _application, window = build_application(
            [],
            data_root=Path(temporary.name) / "fictional-data",
            reminder_authority=LOCAL_REMINDER_AUTHORITY,
        )
        window.remote_reminder_controller = controller

        def cleanup() -> None:
            for thread in tuple(controller._threads):
                if isValid(thread):
                    thread.requestInterruption()
                    thread.quit()
                    thread.wait(5000)
            controller._threads.clear()
            controller._workers.clear()
            window.remote_reminder_controller = None
            window.has_unsaved_changes = False
            window.conflict_active = False
            window._close_ready = True
            window.close()
            window.receipt_refresh_timer.stop()
            window.shell.sidebar.clock_timer.stop()
            window.body_edit.page.deleteLater()
            window.deleteLater()
            self._wait(lambda: not isValid(window), "MainWindow cleanup did not finish")
            temporary.cleanup()

        self.addCleanup(cleanup)
        return window

    def test_owner_untracks_thread_before_native_delete(self) -> None:
        client = _ImmediateClient()
        controller = RemoteReminderController(client)
        _RecordingThread.observations = []
        _RecordingThread.instances = []

        with patch(
            "src.snotes.ui.remote_reminder_controller.QThread",
            _RecordingThread,
        ):
            controller.refresh()
            self._wait(
                lambda: not controller._threads,
                "Remote Reminder worker did not finish",
            )

        thread = _RecordingThread.instances[0]
        self.assertEqual(controller._threads, set())
        self.assertEqual(controller._workers, {})
        self.assertEqual(
            _RecordingThread.observations,
            [(False, False)],
            "QThread native deletion was scheduled before owner bookkeeping cleanup",
        )
        QThread.deleteLater(thread)
        self._wait(lambda: not isValid(thread), "recording QThread was not deleted")

    def test_invalid_stale_thread_does_not_block_main_window_close(self) -> None:
        client = _ImmediateClient()
        controller = RemoteReminderController(client)
        stale = QThread()
        stale.start()
        stale.quit()
        self.assertTrue(stale.wait(5000), "fault-injection QThread did not finish")
        delete(stale)
        self.assertFalse(isValid(stale), "fault injection did not invalidate QThread")
        controller._threads.add(stale)
        controller._workers[stale] = object()
        controller._pending = 0

        window = self._make_window(controller)
        window.show()
        window._close_ready = True
        window._close_pending = True
        window._remote_close_pending = True
        finished = QSignalSpy(controller.shutdownFinished)
        controller.shutdownFinished.connect(window._remote_shutdown_finished)

        controller.begin_shutdown()

        self._wait(lambda: not window.isVisible(), "stale QThread blocked MainWindow close")
        self.assertEqual(finished.count(), 1)
        self.assertFalse(window._remote_close_pending)
        self.assertEqual(controller._threads, set())
        self.assertEqual(controller._workers, {})
        self.assertEqual(client.close_calls, 1)

    def test_valid_finished_stale_thread_is_pruned_during_shutdown(self) -> None:
        client = _ImmediateClient()
        controller = RemoteReminderController(client)
        stale = QThread()
        stale.start()
        stale.quit()
        self.assertTrue(stale.wait(5000), "fault-injection QThread did not finish")
        self.assertTrue(isValid(stale))
        self.assertTrue(stale.isFinished())
        controller._threads.add(stale)
        controller._workers[stale] = object()
        finished = QSignalSpy(controller.shutdownFinished)

        controller.begin_shutdown()

        self.assertEqual(finished.count(), 1)
        self.assertEqual(controller._threads, set())
        self.assertEqual(controller._workers, {})
        self.assertEqual(client.close_calls, 1)
        self._wait(lambda: not isValid(stale), "finished stale QThread was not deleted")

    def test_synchronous_shutdown_prunes_invalid_stale_thread(self) -> None:
        client = _ImmediateClient()
        controller = RemoteReminderController(client)
        stale = QThread()
        stale.start()
        stale.quit()
        self.assertTrue(stale.wait(5000), "fault-injection QThread did not finish")
        delete(stale)
        self.assertFalse(isValid(stale), "fault injection did not invalidate QThread")
        controller._threads.add(stale)
        controller._workers[stale] = object()
        finished = QSignalSpy(controller.shutdownFinished)

        self.assertTrue(controller.shutdown())

        self.assertEqual(finished.count(), 1)
        self.assertEqual(controller._threads, set())
        self.assertEqual(controller._workers, {})
        self.assertEqual(client.close_calls, 1)

    def test_live_worker_is_retained_until_it_finishes(self) -> None:
        client = _BlockedClient()
        controller = RemoteReminderController(client)
        window = self._make_window(controller)
        self.addCleanup(client.release.set)
        window.show()
        window._close_ready = True

        controller.refresh()
        self._wait(client.started.is_set, "Remote Reminder worker did not start")
        live_thread = next(iter(controller._threads))
        self.assertTrue(live_thread.isRunning())

        window.close()
        self._wait(
            lambda: window._remote_close_pending,
            "MainWindow did not enter Remote Reminder shutdown",
        )
        self.assertTrue(window.isVisible())
        self.assertIn(live_thread, controller._threads)
        self.assertIn(live_thread, controller._workers)
        self.assertEqual(client.close_calls, 0)

        client.release.set()
        self._wait(lambda: not window.isVisible(), "live worker completion did not close window")
        self.assertEqual(controller._threads, set())
        self.assertEqual(controller._workers, {})
        self.assertEqual(client.close_calls, 1)


if __name__ == "__main__":
    unittest.main()
