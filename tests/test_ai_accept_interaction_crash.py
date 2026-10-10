"""Native-lifetime regressions around GreenCloud Accept and the Markdown editor."""

from __future__ import annotations

import json
import os
import tempfile
import threading
import time
import unittest
import uuid
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("QTWEBENGINE_CHROMIUM_FLAGS", "--disable-gpu")

from PySide6.QtCore import QEventLoop, QTimer, Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication
from shiboken6 import isValid

from src.snotes.application.local_mcp_write_service import note_version_token
from src.snotes.bootstrap import build_application
from src.snotes.domain.greencloud import GreenCloudSubmission
from src.snotes.domain.models import NoteLocation
from src.snotes.reminder_authority import LOCAL_REMINDER_AUTHORITY


class SlowGreenCloudControl:
    """Offline control whose two worker actions can be released by the test."""

    def __init__(self) -> None:
        self.followup_started = threading.Event()
        self.followup_release = threading.Event()
        self.pull_started = threading.Event()
        self.pull_release = threading.Event()

    def flush_greencloud_decisions(self):
        self.followup_started.set()
        self.followup_release.wait(timeout=10)
        return SimpleNamespace(failed_count=0)

    def check_submissions_now(self):
        self.pull_started.set()
        self.pull_release.wait(timeout=10)
        return SimpleNamespace()

    def last_successful_publish_at(self):
        return ""

    def last_completed_pull_at(self):
        return ""

    def auto_publish_enabled(self):
        return False

    def set_auto_publish_enabled(self, _value):
        return None

    def auto_pull_enabled(self):
        return True

    def set_auto_pull_enabled(self, _value):
        return None


class AiAcceptInteractionCrashTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.application_home = tempfile.TemporaryDirectory(
            prefix="snotes-ai-accept-qt-home-"
        )
        cls.application_environment = patch.dict(
            os.environ,
            {
                "HOME": cls.application_home.name,
                "S_NOTES_DATA_ROOT": str(
                    Path(cls.application_home.name) / "application-data"
                ),
            },
        )
        cls.application_environment.start()
        cls.app = QApplication.instance() or QApplication([])
        cls.app.setQuitOnLastWindowClosed(False)

    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory(
            prefix="snotes-ai-accept-interaction-"
        )
        self.root = Path(self.temporary.name)
        self.environment = patch.dict(
            os.environ,
            {
                "HOME": str(self.root / "home"),
                "S_NOTES_DATA_ROOT": str(self.root / "data"),
            },
        )
        self.environment.start()
        self.control = SlowGreenCloudControl()
        _application, self.window = build_application(
            [],
            data_root=self.root / "data",
            reminder_authority=LOCAL_REMINDER_AUTHORITY,
        )
        self.window.gale_control_service = self.control
        self.release_events: list[threading.Event] = [
            self.control.followup_release,
            self.control.pull_release,
        ]
        self.window.show()

    def tearDown(self) -> None:
        for event in self.release_events:
            event.set()
        dialog = self.window.ai_center_dialog
        if dialog is not None and isValid(dialog):
            self._wait(lambda: not dialog._gale_threads, "AI worker did not finish")
            dialog.close()
            self._wait(
                lambda: self.window.ai_center_dialog is None,
                "AI Center did not close",
            )
        self._wait(
            lambda: self.window._submission_pull_thread is None,
            "startup pull did not finish",
        )
        if isValid(self.window):
            self.window.has_unsaved_changes = False
            self.window.conflict_active = False
            self.window.close()
            self._wait(lambda: not self.window.isVisible(), "main window did not close")
            self.window.deleteLater()
            self._wait(lambda: not isValid(self.window), "main window was not deleted")
        self.environment.stop()
        self.temporary.cleanup()

    @classmethod
    def tearDownClass(cls) -> None:
        QTimer.singleShot(0, cls.app.quit)
        cls.app.exec()
        cls.application_environment.stop()
        cls.application_home.cleanup()

    def _wait(self, predicate, message: str, timeout: float = 15) -> None:
        if predicate():
            return
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

    def _js(self, code: str):
        result: list[object] = []
        self.window.body_edit.page.runJavaScript(code, result.append)
        self._wait(lambda: bool(result), "editor JavaScript did not finish")
        return result[0]

    def _create_note(self, title: str, body: str):
        blank = self.window.note_service.create_note(NoteLocation("项目", "虚构"))
        return self.window.note_service.save_note(
            blank.id,
            title=title,
            content_markdown=body,
            location=blank.location,
            expected_revision=blank.revision,
        )

    def _load(self, note) -> None:
        self.window.switch_note(note.id)
        self._wait(
            lambda: self.window.current_note is not None
            and self.window.current_note.id == note.id
            and self.window.body_edit.can_save
            and self.window.body_edit.latest_markdown == note.content_markdown,
            f"note {note.id} did not load",
        )

    def _submission(
        self,
        operation_type: str,
        request_id: str,
        *,
        note=None,
        payload: dict,
        actor: str = "gale",
    ) -> GreenCloudSubmission:
        return GreenCloudSubmission(
            submission_uuid=str(uuid.uuid4()),
            source_actor=actor,
            request_id=request_id,
            operation_type=operation_type,
            note_id=None if note is None else note.id,
            expected_version=(
                None
                if note is None
                else note_version_token(note.id, note.updated_at)
            ),
            payload=payload,
            created_at="2026-10-09T00:00:00Z",
            delivery_state="delivered",
            delivered_at="2026-10-09T00:00:01Z",
        )

    def _accept_card(self, receipt_id: int) -> None:
        dialog = self.window.ai_center_dialog
        view = dialog.receipt_view
        view.toggle_card(receipt_id)
        card = view.cards[receipt_id]
        if card.has_final_preview:
            card.refresh_final_preview()
            self.assertTrue(card.final_preview_ready, card.preview_status.text())
        QTest.mouseClick(card.accept_button, Qt.LeftButton)
        if self.window.receipt_inbox_service.get_receipt(receipt_id).status != "accepted":
            card = view.cards[receipt_id]
            if card.has_final_preview:
                card.refresh_final_preview()
            QTest.mouseClick(card.accept_button, Qt.LeftButton)
        self._wait(
            lambda: self.window.receipt_inbox_service.get_receipt(receipt_id).status
            == "accepted",
            "receipt was not accepted through its card",
        )

    def _select_title_and_body(self, title_text: str, body_text: str) -> None:
        title = self.window.title_input
        title.setFocus()
        title_start = title.text().index(title_text)
        title.setSelection(title_start, len(title_text))
        self.assertEqual(title.selectedText(), title_text)
        selected = self._js(
            """(() => {
                const root = document.querySelector('.ProseMirror');
                const walker = document.createTreeWalker(root, NodeFilter.SHOW_TEXT);
                let node;
                while ((node = walker.nextNode())) {
                    const start = node.data.indexOf(%s);
                    if (start < 0) continue;
                    root.focus();
                    const range = document.createRange();
                    range.setStart(node, start);
                    range.setEnd(node, start + %d);
                    const selection = window.getSelection();
                    selection.removeAllRanges();
                    selection.addRange(range);
                    return selection.toString();
                }
                return '';
            })()"""
            % (json.dumps(body_text), len(body_text))
        )
        self.assertEqual(selected, body_text)

    def _edit_title_and_body(
        self,
        selected_body: str,
        replacement: str,
        *,
        title_replacement: str,
    ) -> None:
        title = self.window.title_input
        title.setFocus()
        title.setSelection(0, min(5, len(title.text())))
        QTest.keyClicks(title, title_replacement)
        title.setSelection(0, len(title_replacement))
        self.assertEqual(title.selectedText(), title_replacement)
        replaced = self._js(
            """(() => {
                const root = document.querySelector('.ProseMirror');
                const walker = document.createTreeWalker(root, NodeFilter.SHOW_TEXT);
                let node;
                while ((node = walker.nextNode())) {
                    const start = node.data.indexOf(%s);
                    if (start < 0) continue;
                    root.focus();
                    const range = document.createRange();
                    range.setStart(node, start);
                    range.setEnd(node, start + %d);
                    const selection = window.getSelection();
                    selection.removeAllRanges();
                    selection.addRange(range);
                    document.execCommand('insertText', false, %s);
                    return true;
                }
                return false;
            })()"""
            % (json.dumps(selected_body), len(selected_body), json.dumps(replacement))
        )
        self.assertTrue(replaced)
        self._wait(
            lambda: replacement in self.window.body_edit.latest_markdown
            and self.window.has_unsaved_changes,
            "title/body edits did not reach the Markdown owner",
        )

    def _close_normally(self) -> None:
        dialog = self.window.ai_center_dialog
        if dialog is not None:
            dialog.close()
            self._wait(lambda: self.window.ai_center_dialog is None, "AI Center stayed open")
        self.window.close()
        self._wait(lambda: not self.window.isVisible(), "main window stayed open")

    def test_greencloud_create_accept_remains_editable_and_switches_cleanly(self) -> None:
        original = self._create_note("Original note", "Original body\n")
        other = self._create_note("Switch target", "Other body\n")
        self._load(original)
        editor = self.window.body_edit
        editor_identities = (editor.view, editor.page, editor.bridge)
        receipt, _created = self.window.receipt_inbox_service.import_greencloud_pending(
            self._submission(
                "create",
                "interaction-create",
                payload={
                    "title": "Cloud created note",
                    "content": "Created body text\n",
                    "directory_id": None,
                    "main_category": None,
                    "sub_category": None,
                    "tags": [],
                },
            )
        )
        self.window.open_ai_center()
        self._accept_card(receipt.id)
        self._wait(self.control.followup_started.is_set, "follow-up did not start")
        accepted = self.window.note_service.get_note(
            self.window.receipt_inbox_service.get_receipt(receipt.id).note_id
        )
        self.assertEqual(self.window.current_note.id, original.id)

        self._load(accepted)
        self.assertEqual(
            (editor.view, editor.page, editor.bridge), editor_identities
        )
        self.assertEqual(
            self.window._note_load, (accepted.id, editor.current_load_id)
        )
        clean_revision = self.window.current_note.revision
        self._select_title_and_body("Cloud", "Created")
        self.assertFalse(editor.dirty)
        self.assertFalse(self.window.has_unsaved_changes)
        self.assertEqual(self.window.current_note.revision, clean_revision)
        self._edit_title_and_body(
            "Created", "Edited", title_replacement="Local"
        )
        self._load(other)
        persisted = self.window.note_service.get_note(accepted.id)
        self.assertTrue(persisted.title.startswith("Local"))
        self.assertIn("Edited body text", persisted.content_markdown)
        self._load(persisted)

        self.control.followup_release.set()
        self._wait(
            lambda: not self.window.ai_center_dialog._gale_threads,
            "create follow-up worker did not finish",
        )
        self._select_title_and_body("Local", "Edited")
        title = self.window.title_input
        title.setFocus()
        title.setSelection(0, len("Local"))
        QTest.keyClicks(title, "After")
        self._wait(
            lambda: self.window.has_unsaved_changes,
            "post-worker title edit did not reach the note owner",
        )
        self._load(other)
        post_worker = self.window.note_service.get_note(accepted.id)
        self.assertTrue(post_worker.title.startswith("After"))
        self._load(post_worker)
        self._close_normally()

    def test_greencloud_prepend_accept_reloads_and_preserves_editor_consistency(self) -> None:
        target = self._create_note("Target note", "Base body text\n")
        other = self._create_note("Switch target", "Other body\n")
        self.window.ai_access_metadata_service.set_note_visibility(target.id, "full")
        self.window.ai_access_metadata_service.set_note_targets(
            target.id, ("claude-code",)
        )
        target = self.window.note_service.get_note(target.id)
        self._load(target)
        editor = self.window.body_edit
        editor_identities = (editor.view, editor.page, editor.bridge)
        old_load_id = editor.current_load_id
        receipt, _created = self.window.receipt_inbox_service.import_greencloud_pending(
            self._submission(
                "prepend",
                "interaction-prepend",
                note=target,
                actor="claude-code",
                payload={"content": "Cloud head"},
            )
        )
        self.window.open_ai_center()
        self._accept_card(receipt.id)
        self._wait(self.control.followup_started.is_set, "follow-up did not start")
        accepted = self.window.note_service.get_note(target.id)
        self._wait(
            lambda: self.window.current_note.revision == accepted.revision
            and self.window.body_edit.can_save
            and self.window.body_edit.current_load_id != old_load_id
            and self.window.body_edit.latest_markdown == accepted.content_markdown,
            "accepted prepend did not reload a consistent editor document",
        )
        self.assertEqual(self.window.body_edit.baseline, accepted.content_markdown)
        self.assertFalse(self.window.body_edit.dirty)
        self.assertFalse(self.window.has_unsaved_changes)
        self.assertEqual(
            (editor.view, editor.page, editor.bridge), editor_identities
        )
        self.assertEqual(
            self.window._note_load, (accepted.id, editor.current_load_id)
        )

        self._edit_title_and_body(
            "Cloud", "Local", title_replacement="10-08"
        )
        self._load(other)
        persisted = self.window.note_service.get_note(target.id)
        self.assertTrue(persisted.title.startswith("10-08"))
        self.assertIn("Local head", persisted.content_markdown)
        self._load(persisted)

        self.control.followup_release.set()
        self._wait(
            lambda: not self.window.ai_center_dialog._gale_threads,
            "prepend follow-up worker did not finish",
        )
        self._select_title_and_body("10-08", "Local")
        self._close_normally()

    def _native_finish_snapshot(self, thread, worker, owner_snapshot):
        native_finish_entered = threading.Event()
        release_native_finish = threading.Event()
        observations: list[tuple[bool, ...]] = []
        self.release_events.append(release_native_finish)

        def block_native_finish() -> None:
            native_finish_entered.set()
            release_native_finish.wait(timeout=10)

        thread.finished.connect(block_native_finish, Qt.DirectConnection)

        def snapshot_and_release() -> None:
            native_finish_entered.wait(timeout=5)
            observations.append(
                (
                    native_finish_entered.is_set(),
                    not release_native_finish.is_set(),
                    *owner_snapshot(thread, worker),
                    isValid(thread),
                )
            )
            release_native_finish.set()

        timer = threading.Timer(0.15, snapshot_and_release)
        timer.start()
        return observations, timer

    def test_ai_center_retains_worker_and_thread_until_native_finish_returns(self) -> None:
        self.window.open_ai_center()
        dialog = self.window.ai_center_dialog
        action_started = threading.Event()
        release_action = threading.Event()
        self.release_events.append(release_action)

        def action():
            action_started.set()
            release_action.wait(timeout=10)
            return object()

        dialog._launch_gale_worker(
            action, refresh_receipts=False, action_kind="controls"
        )
        self._wait(action_started.is_set, "AI worker did not start")
        thread = dialog._gale_threads[0]
        worker = dialog._gale_workers[0]
        observations, timer = self._native_finish_snapshot(
            thread,
            worker,
            lambda current_thread, current_worker: (
                dialog._gale_threads == [current_thread],
                dialog._gale_workers == [current_worker],
            ),
        )
        release_action.set()
        self._wait(lambda: not dialog._gale_threads, "AI owner did not release worker")
        timer.join(timeout=2)
        self.assertEqual(observations, [(True, True, True, True, True)])
        self._wait(lambda: not isValid(thread), "AI QThread was not deleted")

    def test_startup_pull_retains_worker_and_thread_until_native_finish_returns(self) -> None:
        self.window._pull_submissions_on_open()
        self._wait(self.control.pull_started.is_set, "startup pull worker did not start")
        thread = self.window._submission_pull_thread
        worker = self.window._submission_pull_worker
        observations, timer = self._native_finish_snapshot(
            thread,
            worker,
            lambda current_thread, current_worker: (
                self.window._submission_pull_thread is current_thread,
                self.window._submission_pull_worker is current_worker,
            ),
        )
        self.control.pull_release.set()
        self._wait(
            lambda: self.window._submission_pull_thread is None,
            "startup pull owner did not release worker",
        )
        timer.join(timeout=2)
        self.assertEqual(observations, [(True, True, True, True, True)])
        self._wait(lambda: not isValid(thread), "startup pull QThread was not deleted")


if __name__ == "__main__":
    unittest.main()
