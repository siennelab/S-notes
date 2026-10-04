"""Isolated automated gate for the actual App, never human acceptance."""

from __future__ import annotations

import json
import os
from pathlib import Path
import shutil
import sys
import threading
import traceback
from types import SimpleNamespace
import uuid

from PySide6.QtCore import QEventLoop, QTimer
from shiboken6 import isValid

from src.snotes.application.ai_receipt_inbox_service import AiReceiptAcceptFields
from src.snotes.application.local_mcp_write_service import note_version_token
from src.snotes.bootstrap import build_application
from src.snotes.domain.greencloud import GreenCloudSubmission
from src.snotes.domain.models import NoteLocation
from src.snotes.reminder_authority import resolve_reminder_authority


def main() -> int:
    home = Path(os.environ.get("HOME", "")).resolve()
    root = Path(os.environ.get("S_NOTES_DATA_ROOT", "")).resolve()
    if (
        not home.name.startswith("snotes-step11-")
        or home.parent != Path("/private/tmp")
        or root != home / "data"
    ):
        raise RuntimeError("Step 11 smoke requires isolated temporary HOME/data")
    if shutil.which("node") is not None:
        raise RuntimeError("Step 11 App gate must run without Node on PATH")
    app, window = build_application(
        [value for value in sys.argv if value != "--step11-smoke"],
        data_root=root,
        reminder_authority=resolve_reminder_authority(),
    )
    outcome = {"status": "failed"}
    window.setWindowTitle("S-notes · Step 11 automated fixture")
    window.show()

    def wait_for(predicate) -> None:
        if predicate():
            return
        loop = QEventLoop()
        poll = QTimer()
        poll.timeout.connect(lambda: loop.quit() if predicate() else None)
        deadline = QTimer()
        deadline.setSingleShot(True)
        deadline.timeout.connect(loop.quit)
        poll.start(10)
        deadline.start(5_000)
        loop.exec()
        poll.stop()
        deadline.stop()
        assert predicate(), "fixture background task did not finish"

    def finish() -> None:
        (home / "app-smoke-result.json").write_text(json.dumps(outcome, sort_keys=True))
        if window.ai_center_dialog is not None:
            window.ai_center_dialog.close()
        window.close()
        if outcome["status"] != "passed":
            def exit_failed_fixture() -> None:
                dialog = window.ai_center_dialog
                if dialog is not None and isValid(dialog) and dialog._gale_threads:
                    QTimer.singleShot(50, exit_failed_fixture)
                    return
                app.exit(2)
            QTimer.singleShot(50, exit_failed_fixture)

    def fail(error: Exception) -> None:
        outcome.update(error_type=type(error).__name__, error=str(error))
        traceback.print_exception(error)
        print("STEP11_FORMAL_APP_AUTOMATED_SMOKE=FAIL:" + type(error).__name__, flush=True)
        finish()

    def execute() -> None:
        try:
            receipts = window.receipt_inbox_service
            assert receipts is not None
            assert window._automatic_submission_pull is False
            window.directory_service.create_directory("Step 11 fictional")
            location = NoteLocation("Step 11 fictional", "无")

            def import_proposal(operation, payload, note=None):
                return receipts.import_greencloud_pending(GreenCloudSubmission(
                    submission_uuid=str(uuid.uuid4()), source_actor="claude-code",
                    request_id=uuid.uuid4().hex, operation_type=operation,
                    note_id=note.id if note is not None else None,
                    expected_version=note_version_token(note.id, note.updated_at) if note is not None else None,
                    payload=payload, created_at="2026-10-04T00:00:00+00:00",
                    delivery_state="delivered", delivered_at="2026-10-04T00:00:01+00:00",
                ))[0]

            def accept(receipt):
                result = receipts.accept_receipt(receipt.id, AiReceiptAcceptFields(
                    title=receipt.title, body=receipt.body,
                    main_category=receipt.main_category, sub_category=receipt.sub_category,
                    tags=receipt.tags, visibility=receipt.ai_visibility,
                    target_ids=("claude-code",),
                ))
                assert result.receipt.status == "accepted"
                return window.note_service.get_note(result.receipt.note_id)

            create = import_proposal("create", {
                "title": "Step 11 fictional App smoke", "content": "# Fixture\n\nunique alpha\n",
                "directory_id": None, "main_category": location.main,
                "sub_category": location.sub, "tags": ["fictional"],
            })
            window.open_ai_center()
            card = window.ai_center_dialog.receipt_view.cards[create.id]
            assert "GreenCloud" in card.summary_label.text()
            assert "claude-code" in card.summary_label.text()
            note = accept(create)
            assert note is not None
            repeated = accept(create)
            assert repeated.id == note.id and len(window.note_service.list_active_notes()) == 1
            append = import_proposal("append", {"content": "\nappended fixture\n"}, note)
            note = accept(append)
            edit = import_proposal("edit", {
                "edits": [{"old_markdown": "unique alpha", "new_markdown": "unique beta"}]
            }, note)
            note = accept(edit)
            assert "unique beta" in note.content_markdown and "appended fixture" in note.content_markdown
            assert receipts.canonicalize_markdown(note.content_markdown) == note.content_markdown
            original_body = note.content_markdown
            original_revision = note.revision
            prepend = import_proposal("prepend", {"content": "prepended fixture\n\n"}, note)
            note = accept(prepend)
            assert note.content_markdown.startswith("prepended fixture")
            assert note.content_markdown.count("prepended fixture") == 1
            assert original_body in note.content_markdown
            assert note.revision > original_revision
            prepended_body = note.content_markdown
            prepended_revision = note.revision
            destination = window.directory_service.create_directory("Step 11 metadata fictional")
            metadata = import_proposal("update_note_metadata", {
                "title": "Step 11 metadata App smoke",
                "directory_id": destination.id,
                "add_tags": ["metadata-fixture"], "remove_tags": ["fictional"],
            }, note)
            note = accept(metadata)
            assert note.title == "Step 11 metadata App smoke"
            assert note.directory_id == destination.id
            assert note.content_markdown == prepended_body
            assert note.revision > prepended_revision
            assert "metadata-fixture" in window.tag_service.get_note_tags(note.id)
            assert "fictional" not in window.tag_service.get_note_tags(note.id)
            outcome.update(create_once=True, append=True, edit=True, zero_diff=True,
                           prepend=True, metadata=True, metadata_body_unchanged=True,
                           source_actor_ui=True, node_on_path=False, real_user_database_opened=False)
            dialog = window.ai_center_dialog
            control = dialog.gale
            started = threading.Event()
            release = threading.Event()
            completed = threading.Event()
            original_followup = control.flush_greencloud_decisions

            def slow_fixture_followup():
                started.set()
                assert release.wait(5), "fixture followup was not released"
                completed.set()
                return SimpleNamespace(failed_count=0)

            def verify_deleted_and_finish() -> None:
                try:
                    assert completed.is_set(), "followup must complete before deletion"
                    assert not isValid(dialog), "dialog must be naturally deleted"
                    assert isValid(window) and window.isVisible(), "main window must remain visible"
                    outcome.update(status="passed", close_phase="passed",
                                   ai_center_close_during_followup=True, main_window_stays_open=True)
                    print("STEP11_FORMAL_APP_AUTOMATED_SMOKE=PASS", flush=True)
                    finish()
                except Exception as error:
                    fail(error)

            def close_deadline() -> None:
                if outcome["status"] != "passed" and "error_type" not in outcome:
                    fail(AssertionError("fixture dialog did not close after followup"))

            # Return to the ordinary App event loop before checking DeferredDelete.
            dialog.destroyed.connect(lambda: QTimer.singleShot(0, verify_deleted_and_finish))
            control.flush_greencloud_decisions = slow_fixture_followup
            try:
                outcome["close_phase"] = "starting_followup"
                dialog.receipt_view.terminalDecisionSaved.emit()
                wait_for(started.is_set)
                outcome["close_phase"] = "closing_dialog"
                dialog.close_button.click()
                assert isValid(dialog) and not dialog.isVisible(), "dialog must hide without deletion"
                assert isValid(window) and window.isVisible(), "main window must remain visible"
                outcome["close_phase"] = "waiting_for_deferred_delete"
                QTimer.singleShot(50, release.set)
                QTimer.singleShot(5_000, close_deadline)
            except Exception:
                release.set()
                raise
            finally:
                control.flush_greencloud_decisions = original_followup
        except Exception as error:
            fail(error)

    QTimer.singleShot(300, execute)
    app.exec()
    return 0 if outcome["status"] == "passed" else 2
