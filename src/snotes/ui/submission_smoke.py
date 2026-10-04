"""Isolated automated gate for the actual App, never human acceptance."""

from __future__ import annotations

import json
import os
from pathlib import Path
import shutil
import sys
import uuid

from PySide6.QtCore import QTimer

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
            outcome.update(status="passed", create_once=True, append=True, edit=True, zero_diff=True,
                           source_actor_ui=True, node_on_path=False, real_user_database_opened=False)
            print("STEP11_FORMAL_APP_AUTOMATED_SMOKE=PASS", flush=True)
        except Exception as error:
            outcome["error_type"] = type(error).__name__
            print("STEP11_FORMAL_APP_AUTOMATED_SMOKE=FAIL:" + type(error).__name__, flush=True)
        finally:
            (home / "app-smoke-result.json").write_text(json.dumps(outcome, sort_keys=True))
            if window.ai_center_dialog is not None:
                window.ai_center_dialog.close()
            window.close()

    QTimer.singleShot(300, execute)
    app.exec()
    return 0 if outcome["status"] == "passed" else 2
