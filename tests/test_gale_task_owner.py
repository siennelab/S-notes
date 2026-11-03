"""Shared Gale lifecycle close barriers, independently of receipt business logic."""

from __future__ import annotations

import ast
from pathlib import Path
import threading
import unittest

from PySide6.QtCore import QCoreApplication, QEventLoop, QTimer
from PySide6.QtTest import QSignalSpy

from src.snotes.ui.gale_task_owner import GaleTaskOwner


class GaleTaskOwnerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QCoreApplication.instance() or QCoreApplication([])

    @classmethod
    def tearDownClass(cls):
        QTimer.singleShot(0, cls.app.quit)
        cls.app.exec()

    def setUp(self):
        self.owner = GaleTaskOwner()
        self.releases = []

    def tearDown(self):
        for release in self.releases:
            release.set()
        self._wait(lambda: not self.owner.has_tasks)
        self.owner.deleteLater()

    def _wait(self, predicate):
        if predicate():
            return
        loop = QEventLoop()
        timer = QTimer(loop)
        timer.timeout.connect(lambda: loop.quit() if predicate() else None)
        timer.start(10)
        QTimer.singleShot(3000, loop, loop.quit)
        loop.exec()
        timer.stop()
        self.assertTrue(predicate(), "Gale task owner did not settle")

    def _blocked_action(self):
        started, release = threading.Event(), threading.Event()
        self.releases.append(release)

        def action():
            started.set()
            self.assertTrue(release.wait(3), "fixture action was not released")

        return action, started, release

    def test_queued_followup_drains_once_before_idle_and_close_ready(self):
        owner = self.owner
        idle, close_ready = QSignalSpy(owner.idle), QSignalSpy(owner.dialogCloseReady)
        action, started, release = self._blocked_action()
        owner.start(action)
        self._wait(started.is_set)
        calls = []
        followup = lambda: calls.append("followup")
        self.assertTrue(owner.queue_followup(followup))
        self.assertTrue(owner.queue_followup(followup))
        self.assertFalse(owner.request_dialog_close())
        self.assertTrue(owner.has_tasks)
        self.assertTrue(owner.followup_queued)
        self.assertEqual(idle.count(), 0)
        release.set()
        self._wait(lambda: close_ready.count() == 1)
        self.assertEqual(calls, ["followup"])
        self.assertEqual(idle.count(), 1)
        self.assertFalse(owner.has_tasks)
        self.assertFalse(owner.followup_queued)
        self.assertFalse(owner.dialog_close_pending)

    def test_startup_pull_does_not_serialize_dialog_work_or_block_dialog_close(self):
        owner = self.owner
        idle = QSignalSpy(owner.idle)
        action, started, release = self._blocked_action()
        owner.start(action, action_kind="startup_pull")
        self._wait(started.is_set)
        completed = threading.Event()
        self.assertFalse(owner.queue_followup(completed.set))
        self._wait(lambda: completed.is_set() and not owner.has_dialog_tasks)
        self.assertTrue(owner.is_running("startup_pull"))
        self.assertTrue(owner.has_tasks)
        self.assertTrue(owner.request_dialog_close())
        self.assertEqual(idle.count(), 0)
        release.set()
        self._wait(lambda: idle.count() == 1)

    def test_reopening_cancels_deferred_dialog_close(self):
        owner = self.owner
        close_ready = QSignalSpy(owner.dialogCloseReady)
        action, started, release = self._blocked_action()
        owner.start(action)
        self._wait(started.is_set)
        self.assertFalse(owner.request_dialog_close())
        owner.cancel_dialog_close()
        release.set()
        self._wait(lambda: not owner.has_tasks)
        self.assertFalse(owner.dialog_close_pending)
        self.assertEqual(close_ready.count(), 0)

    def test_business_result_and_error_contract_pass_through_and_release(self):
        owner = self.owner
        results = QSignalSpy(owner.actionFinished)
        result = object()
        owner.start(lambda: result, refresh_receipts=True)
        self._wait(lambda: not owner.has_tasks)
        self.assertEqual(results.at(0), [result, None, True, "controls"])

        class ActionError(Exception):
            code = "fixture_failure"

        def failed():
            raise ActionError("private details")

        owner.queue_followup(failed)
        self._wait(lambda: not owner.has_tasks)
        self.assertEqual(results.at(1), [None, "fixture_failure", False, "greencloud_followup"])

    def test_windows_delegate_worker_lifetime_and_do_not_read_private_task_state(self):
        root = Path(__file__).resolve().parents[1] / "src/snotes/ui"
        private_state = {
            "_gale_threads", "_gale_workers", "_greencloud_followup_running",
            "_greencloud_followup_queued", "_submission_pull_thread", "_submission_pull_worker",
        }
        for name in ("ai_center_dialog.py", "main_window.py"):
            with self.subTest(file=name):
                tree = ast.parse((root / name).read_text())
                attributes = {node.attr for node in ast.walk(tree) if isinstance(node, ast.Attribute)}
                constructors = {
                    node.func.id for node in ast.walk(tree)
                    if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
                }
                self.assertFalse(attributes & private_state)
                self.assertFalse(constructors & {"QThread", "GaleWorker"})


if __name__ == "__main__":
    unittest.main()
