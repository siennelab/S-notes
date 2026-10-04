"""Step 11 AI Center behavior over fictional data in a temporary database."""

from __future__ import annotations

import os
import tempfile
import threading
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("QTWEBENGINE_CHROMIUM_FLAGS", "--disable-gpu")

from PySide6.QtCore import QTimer
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QMessageBox

from src.snotes.application.ai_access_metadata_service import AiAccessMetadataService
from src.snotes.application.ai_audit_service import AiAuditService
from src.snotes.application.ai_receipt_inbox_service import AiReceiptInboxService
from src.snotes.application.ai_safe_read_service import AiSafeReadService
from src.snotes.application.ai_write_authorization import AiWriteAuthorization
from src.snotes.application.directory_service import DirectoryService
from src.snotes.application.local_mcp_write_service import (
    LocalMcpWriteService,
    note_version_token,
)
from src.snotes.application.note_service import NoteService
from src.snotes.application.tag_service import TagService
from src.snotes.domain.greencloud import GreenCloudSubmission
from src.snotes.domain.models import NoteLocation
from src.snotes.storage.repositories.ai_audit import SQLiteAiAuditRepository
from src.snotes.storage.repositories.ai_mcp_operations import (
    SQLiteAiMcpOperationRepository,
)
from src.snotes.storage.repositories.ai_receipts import SQLiteAiReceiptInboxRepository
from src.snotes.storage.repositories.directories import SQLiteDirectoriesRepository
from src.snotes.storage.repositories.notes import SQLiteNotesRepository
from src.snotes.storage.repositories.tags import SQLiteTagsRepository
from src.snotes.storage.schema import initialize_database
from src.snotes.ui.ai_center_dialog import AiCenterDialog, ReceiptInboxView
from tests.canonicalizer_test_double import CanonicalizerTestDouble


class FakeGaleControl:
    def __init__(self) -> None:
        self.auto_publish = False
        self.auto_pull = True
        self.followup_calls = 0
        self.followup_started = threading.Event()
        self.followup_release = threading.Event()
        self.followup_release.set()
        self.failed_count = 0

    def manual_update(self):
        return object()

    def check_submissions_now(self):
        return object()

    def flush_greencloud_decisions(self):
        self.followup_calls += 1
        self.followup_started.set()
        self.followup_release.wait(timeout=2)
        return SimpleNamespace(failed_count=self.failed_count)

    def last_successful_publish_at(self):
        return ""

    def last_completed_pull_at(self):
        return ""

    def auto_publish_enabled(self):
        return self.auto_publish

    def set_auto_publish_enabled(self, value):
        self.auto_publish = bool(value)

    def auto_pull_enabled(self):
        return self.auto_pull

    def set_auto_pull_enabled(self, value):
        self.auto_pull = bool(value)


class GC35Step11UiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.test_home = tempfile.TemporaryDirectory(prefix="snotes-step11-ui-home-")
        cls.environment = patch.dict(
            os.environ,
            {
                "HOME": cls.test_home.name,
                "S_NOTES_DATA_ROOT": cls.test_home.name,
                "PATH": "/usr/bin:/bin",
            },
        )
        cls.environment.start()
        cls.app = QApplication.instance() or QApplication([])
        cls.app.setQuitOnLastWindowClosed(False)

    @classmethod
    def tearDownClass(cls) -> None:
        QTimer.singleShot(0, cls.app.quit)
        cls.app.exec()
        cls.environment.stop()
        cls.test_home.cleanup()

    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory(prefix="snotes-step11-ui-db-")
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.connections = initialize_database(self.root / "fictional.db")
        directory_repository = SQLiteDirectoriesRepository(self.connections)
        tag_repository = SQLiteTagsRepository(self.connections)
        note_repository = SQLiteNotesRepository(
            self.connections, directory_repository
        )
        self.notes = NoteService(note_repository)
        self.tags = TagService(tag_repository)
        self.directories = DirectoryService(directory_repository)
        self.metadata = AiAccessMetadataService(note_repository)
        self.receipts = AiReceiptInboxService(
            SQLiteAiReceiptInboxRepository(
                self.connections, directory_repository, tag_repository
            ),
            notes=self.notes,
            tags=self.tags,
            ai_metadata=self.metadata,
            mcp_operations=SQLiteAiMcpOperationRepository(
                self.connections, tag_repository
            ),
            canonicalizer=CanonicalizerTestDouble(
                lambda markdown: markdown.strip() + "\n"
            ),
            authorization=AiWriteAuthorization(directories=self.directories),
        )
        audit = AiAuditService(SQLiteAiAuditRepository(self.connections))
        safe_reads = AiSafeReadService(self.root / "unused-redaction-rules.json")
        self.writers = {
            actor: LocalMcpWriteService(
                actor_target_id=actor,
                notes=self.notes,
                directories=self.directories,
                tags=self.tags,
                ai_metadata=self.metadata,
                safe_reads=safe_reads,
                audit=audit,
                receipt_inbox=self.receipts,
            )
            for actor in ("claude-code", "clawbot")
        }
        # Materialize a fictional destination without touching the real database.
        self.notes.create_note(NoteLocation("项目", "AI 提案"))
        with self.connections.write() as connection:
            for operation in ("create", "modify"):
                connection.execute(
                    """
                    INSERT OR REPLACE INTO app_settings(
                        setting_key, setting_value, updated_at
                    ) VALUES (?, '1', '2026-10-04T00:00:00+00:00')
                    """,
                    (f"ai_{operation}_requires_confirmation",),
                )

    def _pending_create(self, actor: str, source: str, suffix: str) -> int:
        result = self.writers[actor].create_note(
            request_id=f"step11-{suffix}",
            title=f"Fictional {suffix}",
            content=f"proposal {suffix}",
            main_category="项目",
            sub_category="AI 提案",
        )
        self.assertEqual(result["status"], "pending_confirmation", result)
        receipt_id = int(result["proposal_id"])
        with self.connections.write() as connection:
            connection.execute(
                "UPDATE ai_receipt_inbox SET source=? WHERE id=?",
                (source, receipt_id),
            )
        return receipt_id

    def _wait_for(self, predicate, message: str = "Qt worker did not finish") -> None:
        for _attempt in range(300):
            self.app.processEvents()
            if predicate():
                return
            QTest.qWait(10)
        self.fail(message)

    def _close_dialog(self, dialog: AiCenterDialog) -> None:
        self._wait_for(lambda: not dialog._gale_threads)
        dialog.close()
        self.app.processEvents()

    def test_render_identifies_source_actor_operation_and_proposal(self) -> None:
        cloud_id = self._pending_create("claude-code", "GreenCloud", "cloud-render")
        gale_id = self._pending_create("clawbot", "Gale", "gale-render")
        dialog = AiCenterDialog(
            self.receipts,
            self.metadata,
            gale=FakeGaleControl(),
        )
        self.addCleanup(self._close_dialog, dialog)
        dialog.resize(1000, 760)
        dialog.show()
        self.app.processEvents()

        cloud = dialog.receipt_view.cards[cloud_id]
        gale = dialog.receipt_view.cards[gale_id]
        self.assertIn("来源：GreenCloud", cloud.summary_label.text())
        self.assertIn("Actor：claude-code", cloud.summary_label.text())
        self.assertIn("操作：新建笔记", cloud.summary_label.text())
        self.assertTrue(cloud.bulk_reject_button.isVisible())
        dialog.receipt_view.toggle_card(cloud_id)
        self.assertEqual(cloud.body_edit.toPlainText(), "proposal cloud-render")
        self.assertIn("来源：Oracle Gale", gale.summary_label.text())
        self.assertFalse(gale.bulk_reject_button.isVisible())

        screenshot = dialog.grab()
        self.assertFalse(screenshot.isNull())
        self.assertGreaterEqual(screenshot.width(), 900)
        self.assertGreaterEqual(screenshot.height(), 700)
        output = os.environ.get("SNOTES_STEP11_RENDER_OUTPUT", "").strip()
        if output:
            self.assertTrue(screenshot.save(output, "PNG"))

    def test_cloud_new_operations_render_with_existing_previews(self) -> None:
        blank = self.notes.create_note(NoteLocation("项目", "AI 提案"))
        note = self.notes.save_note(
            blank.id,
            title="Fictional new operations",
            content_markdown="base body\n",
            location=blank.location,
            expected_revision=blank.revision,
        )
        self.metadata.set_note_visibility(note.id, "full")
        self.metadata.set_note_targets(note.id, ("claude-code",))
        note = self.notes.get_note(note.id)
        assert note is not None

        def submission(operation: str, request_id: str, payload: dict):
            return GreenCloudSubmission(
                submission_uuid=f"uuid-{request_id}",
                source_actor="claude-code",
                request_id=request_id,
                operation_type=operation,
                note_id=note.id,
                expected_version=note_version_token(note.id, note.updated_at),
                payload=payload,
                created_at="2026-10-04T00:00:00Z",
                delivery_state="delivered",
                delivered_at="2026-10-04T00:00:01Z",
            )

        prepend, _ = self.receipts.import_greencloud_pending(
            submission("prepend", "ui-prepend", {"content": "cloud head"})
        )
        metadata, _ = self.receipts.import_greencloud_pending(
            submission(
                "update_note_metadata",
                "ui-metadata",
                {"title": "Cloud title"},
            )
        )
        no_change, _ = self.receipts.import_greencloud_pending(
            submission("update_note_metadata", "ui-metadata-empty", {})
        )
        view = ReceiptInboxView(self.receipts, self.metadata)
        self.addCleanup(view.close)
        self.assertIn("操作：前置追加", view.cards[prepend.id].summary_label.text())
        self.assertIn("操作：修改资料", view.cards[metadata.id].summary_label.text())
        metadata_card = view.cards[metadata.id]
        self.assertTrue(metadata_card.is_pending_metadata)
        metadata_card.refresh_metadata_preview()
        self.assertTrue(metadata_card.metadata_preview_ready)
        self.assertIn("标题：Cloud title", metadata_card.metadata_preview.text())
        no_change_card = view.cards[no_change.id]
        no_change_card.refresh_metadata_preview()
        self.assertTrue(no_change_card.metadata_preview_ready)
        self.assertIn("没有实际资料变化", no_change_card.metadata_preview.text())

    def test_bulk_reject_is_scoped_to_one_source_and_actor(self) -> None:
        selected = {
            self._pending_create("claude-code", "GreenCloud", "bulk-one"),
            self._pending_create("claude-code", "GreenCloud", "bulk-two"),
        }
        other_actor = self._pending_create(
            "clawbot", "GreenCloud", "bulk-other-actor"
        )
        other_source = self._pending_create(
            "claude-code", "Gale", "bulk-other-source"
        )
        view = ReceiptInboxView(self.receipts, self.metadata)
        self.addCleanup(view.close)
        terminal_signals = []
        view.terminalDecisionSaved.connect(lambda: terminal_signals.append(True))

        view.reject_pending_by_actor("GreenCloud", "claude-code")

        pending_ids = {item.id for item in self.receipts.list_pending_receipts()}
        self.assertTrue(selected.isdisjoint(pending_ids))
        self.assertIn(other_actor, pending_ids)
        self.assertIn(other_source, pending_ids)
        self.assertEqual(
            {self.receipts.get_receipt(item).status for item in selected},
            {"discarded"},
        )
        self.assertEqual(terminal_signals, [True])

    def test_conflict_keeps_current_body_and_still_queues_terminal_followup(self) -> None:
        blank = self.notes.create_note(NoteLocation("项目", "AI 提案"))
        original = self.notes.save_note(
            blank.id,
            title="Fictional conflict",
            content_markdown="base\n",
            location=blank.location,
            expected_revision=blank.revision,
        )
        self.metadata.set_note_visibility(original.id, "full")
        self.metadata.set_note_targets(original.id, ("claude-code",))
        original = self.notes.get_note(original.id)
        self.assertIsNotNone(original)
        proposal = self.writers["claude-code"].append_note(
            request_id="step11-conflict",
            note_id=original.id,
            content="cloud addition",
            expected_version=note_version_token(original.id, original.updated_at),
        )
        self.assertEqual(proposal["status"], "pending_confirmation", proposal)
        receipt_id = int(proposal["proposal_id"])
        with self.connections.write() as connection:
            connection.execute(
                "UPDATE ai_receipt_inbox SET source='GreenCloud' WHERE id=?",
                (receipt_id,),
            )
            connection.execute(
                """
                UPDATE notes
                SET content_markdown='human current body\n',
                    revision=revision + 1,
                    updated_at='2030-01-01T00:00:00+00:00'
                WHERE id=?
                """,
                (original.id,),
            )
        view = ReceiptInboxView(self.receipts, self.metadata)
        self.addCleanup(view.close)
        terminal_signals = []
        view.terminalDecisionSaved.connect(lambda: terminal_signals.append(True))

        with patch.object(QMessageBox, "warning"):
            view.accept_receipt(receipt_id)

        self.assertEqual(
            self.notes.get_note(original.id).content_markdown,
            "human current body\n",
        )
        processed = self.receipts.get_receipt(receipt_id)
        self.assertTrue(processed.processed)
        self.assertTrue(self.receipts.receipt_outcome(processed).is_conflict)
        self.assertEqual(terminal_signals, [True])
        self.assertEqual(view.list_filter, "processed")
        self.assertIn("版本冲突 · 未写入", view.cards[receipt_id].summary_label.text())

    def test_followup_runs_off_ui_thread_coalesces_busy_requests_and_reports_retry(self) -> None:
        gale = FakeGaleControl()
        dialog = AiCenterDialog(self.receipts, self.metadata, gale=gale)
        self.addCleanup(self._close_dialog, dialog)
        gale.followup_started.clear()
        gale.followup_release.clear()

        dialog.receipt_view.terminalDecisionSaved.emit()
        self._wait_for(gale.followup_started.is_set)
        self.assertTrue(dialog.isEnabled())
        dialog.receipt_view.terminalDecisionSaved.emit()
        self.assertTrue(dialog._greencloud_followup_queued)
        gale.followup_release.set()
        self._wait_for(
            lambda: gale.followup_calls == 2 and not dialog._gale_threads,
            "queued GreenCloud follow-up was lost",
        )
        self.assertEqual(dialog.gale_status.text(), "云端回执与镜像已同步")

        gale.failed_count = 1
        dialog.receipt_view.terminalDecisionSaved.emit()
        self._wait_for(
            lambda: gale.followup_calls == 3 and not dialog._gale_threads
        )
        self.assertEqual(dialog.gale_status.text(), "决定已保存，云端同步待重试")


if __name__ == "__main__":
    unittest.main()
