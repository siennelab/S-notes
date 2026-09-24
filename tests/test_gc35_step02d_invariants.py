"""Step 2D invariants for active Note body writes on disposable data only."""

from __future__ import annotations

import os
import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from unittest.mock import patch

from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QApplication

from src.snotes.application.ai_access_metadata_service import AiAccessMetadataService
from src.snotes.application.ai_audit_service import AiAuditService
from src.snotes.application.ai_receipt_inbox_service import AiReceiptAcceptFields, AiReceiptInboxService
from src.snotes.application.ai_safe_read_service import AiSafeReadService
from src.snotes.application.directory_service import DirectoryService
from src.snotes.application.local_mcp_service import LocalMcpService
from src.snotes.application.local_mcp_write_service import LocalMcpWriteService, note_version_token
from src.snotes.application.note_service import NoteService
from src.snotes.application.tag_service import TagService
from src.snotes.domain.models import Note, NoteLocation
from src.snotes.storage.repositories.ai_audit import SQLiteAiAuditRepository
from src.snotes.storage.repositories.ai_mcp_operations import SQLiteAiMcpOperationRepository
from src.snotes.storage.repositories.ai_receipts import SQLiteAiReceiptInboxRepository
from src.snotes.storage.repositories.directories import SQLiteDirectoriesRepository
from src.snotes.storage.repositories.notes import SQLiteNotesRepository
from src.snotes.storage.repositories.tags import SQLiteTagsRepository
from src.snotes.storage.schema import initialize_database
from src.snotes.ui.markdown_canonicalizer import CanonicalizationError, MarkdownCanonicalizer


class Step02DInvariantTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.home = tempfile.TemporaryDirectory(prefix="snotes-2d-home-")
        cls.environment = patch.dict(os.environ, {
            "HOME": cls.home.name,
            "S_NOTES_DATA_ROOT": cls.home.name,
            "QT_QPA_PLATFORM": "offscreen",
            "QTWEBENGINE_CHROMIUM_FLAGS": "--disable-gpu",
            "PATH": "/usr/bin:/bin",
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

    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="snotes-2d-db-")
        self.addCleanup(temporary.cleanup)
        root = Path(temporary.name)
        self.connections = initialize_database(root / "fixture.db")
        directories_repository = SQLiteDirectoriesRepository(self.connections)
        tags_repository = SQLiteTagsRepository(self.connections)
        notes_repository = SQLiteNotesRepository(self.connections, directories_repository)
        self.notes = NoteService(notes_repository)
        self.tags = TagService(tags_repository)
        self.metadata = AiAccessMetadataService(notes_repository)
        audit = AiAuditService(SQLiteAiAuditRepository(self.connections))
        safe_reads = AiSafeReadService(root / "unused-rules.json")
        self.receipts = AiReceiptInboxService(
            SQLiteAiReceiptInboxRepository(self.connections, directories_repository, tags_repository),
            notes=self.notes, tags=self.tags, ai_metadata=self.metadata,
            mcp_operations=SQLiteAiMcpOperationRepository(self.connections, tags_repository),
        )
        shared = dict(
            actor_target_id="clawbot", notes=self.notes,
            directories=DirectoryService(directories_repository), tags=self.tags,
            ai_metadata=self.metadata, safe_reads=safe_reads, audit=audit,
            receipt_inbox=self.receipts,
        )
        self.writes = LocalMcpWriteService(**shared)
        self.reads = LocalMcpService(**shared)
        self._confirmation(False, "modify")
        self._confirmation(False, "create")

    def _confirmation(self, required: bool, operation: str):
        with self.connections.write() as connection:
            connection.execute(
                "INSERT OR REPLACE INTO app_settings(setting_key, setting_value, updated_at) VALUES (?, ?, ?)",
                (f"ai_{operation}_requires_confirmation", str(int(required)), "2026-01-01T00:00:00"),
            )

    @staticmethod
    def _token(note: Note) -> str:
        return note_version_token(note.id, note.updated_at)

    def _snapshot(self):
        with self.connections.read() as connection:
            return tuple(tuple(row) for row in connection.execute("SELECT * FROM notes ORDER BY id"))

    def _assert_new_body(self, note: Note, body: str):
        self.assertEqual(note.content_markdown, body)
        self.assertEqual(note.revision, 1)
        self.assertTrue(note.updated_at)
        self.assertEqual(datetime.fromisoformat(note.updated_at).isoformat(), note.updated_at)
        self.assertTrue(self._token(note))

    def _assert_body_update(self, before: Note, after: Note, body: str):
        self.assertEqual(after.id, before.id)
        self.assertEqual(after.content_markdown, body)
        self.assertNotEqual(after.content_markdown, before.content_markdown)
        self.assertEqual(after.revision, before.revision + 1)
        self.assertGreater(datetime.fromisoformat(after.updated_at), datetime.fromisoformat(before.updated_at))
        self.assertNotEqual(self._token(after), self._token(before))

    def _existing(self, body="base\n") -> Note:
        blank = self.notes.create_note(NoteLocation())
        saved = self.notes.save_note(
            blank.id, title="Fictional", content_markdown=body,
            location=blank.location, expected_revision=blank.revision,
        )
        self.metadata.set_note_visibility(saved.id, "full")
        self.metadata.set_note_targets(saved.id, ("clawbot",))
        return self.notes.get_note(saved.id)

    def _accept(self, proposal_id: int):
        receipt = self.receipts.get_receipt(proposal_id)
        return self.receipts.accept_receipt(proposal_id, AiReceiptAcceptFields(
            title=receipt.title, body=receipt.body,
            main_category=receipt.main_category, sub_category=receipt.sub_category,
            tags=receipt.tags, visibility=receipt.ai_visibility,
            target_ids=("clawbot",),
        ))

    def test_ai_direct_create_initial_revision_timestamp_and_token(self):
        self.assertEqual(self._snapshot(), ())
        result = self.writes.create_note("direct-create", "Fictional", "- created")
        self.assertEqual(result["status"], "created")
        note = self.notes.get_note(result["note_id"])
        self._assert_new_body(note, "* created\n")
        second = self.writes.create_note("direct-create-two", "Fictional", "- second")
        self.assertEqual(second["status"], "created")
        second_note = self.notes.get_note(second["note_id"])
        self._assert_new_body(second_note, "* second\n")
        self.assertNotEqual(self._token(note), self._token(second_note))

    def test_create_acceptance_initial_revision_timestamp_and_token(self):
        self._confirmation(True, "create")
        result = self.writes.create_note("pending-create", "Fictional", "- accepted")
        self.assertEqual(result["status"], "pending_confirmation")
        self.assertEqual(self._snapshot(), ())
        accepted = self._accept(result["proposal_id"])
        self._assert_new_body(accepted.note, "* accepted\n")
        self.assertEqual(self.notes.get_note(accepted.note.id), accepted.note)

    def test_direct_append_and_prepend_each_bump_body_version(self):
        for operation, body in (("append", "base\n\nnext\n"), ("prepend", "next\n\nbase\n")):
            with self.subTest(operation=operation):
                before = self._existing()
                result = getattr(self.writes, f"{operation}_note")(
                    request_id=f"direct-{operation}", note_id=before.id, content="next",
                    expected_version=self._token(before),
                )
                self.assertEqual(result["status"], "updated")
                self._assert_body_update(before, self.notes.get_note(before.id), body)

    def test_pending_append_acceptance_bumps_body_version(self):
        self._confirmation(True, "modify")
        before = self._existing()
        result = self.writes.append_note(
            request_id="pending-append", note_id=before.id, content="next",
            expected_version=self._token(before),
        )
        self.assertEqual(result["status"], "pending_confirmation")
        self.assertEqual(self.notes.get_note(before.id), before)
        accepted = self._accept(result["proposal_id"])
        self._assert_body_update(before, accepted.note, "base\n\nnext\n")

    def test_gui_blank_creation_then_body_save_bumps_version(self):
        # MainWindow.create_note and save_current_note delegate to these NoteService calls.
        blank = self.notes.create_note(NoteLocation())
        self._assert_new_body(blank, "")
        saved = self.notes.save_note(
            blank.id, title="Fictional", content_markdown="written\n",
            location=blank.location, expected_revision=blank.revision,
        )
        self._assert_body_update(blank, saved, "written\n")

    def test_gui_existing_body_save_bumps_version(self):
        before = self._existing()
        saved = self.notes.save_note(
            before.id, title=before.title, content_markdown="edited\n",
            location=before.location, expected_revision=before.revision,
        )
        self._assert_body_update(before, saved, "edited\n")

    def test_stale_version_rejects_without_note_mutation(self):
        before = self._existing()
        snapshot = self._snapshot()
        result = self.writes.append_note(
            request_id="stale-append", note_id=before.id, content="next",
            expected_version="snotes-v1-stale",
        )
        self.assertEqual(result["status"], "conflict")
        self.assertEqual(self._snapshot(), snapshot)

    def test_canonicalizer_failure_leaves_notes_unchanged(self):
        before = self._existing()
        snapshot = self._snapshot()
        with patch.object(MarkdownCanonicalizer, "canonicalize_markdown",
                          side_effect=CanonicalizationError("fixture failure")):
            failed_create = self.writes.create_note(
                request_id="failed-create", title="Fictional", content="- input",
            )
            result = self.writes.append_note(
                request_id="failed-append", note_id=before.id, content="next",
                expected_version=self._token(before),
            )
        self.assertEqual(failed_create["status"], "failed")
        self.assertEqual(result["status"], "pending_confirmation")
        self.assertEqual(self._snapshot(), snapshot)

    def test_markdown_read_leaves_note_unchanged(self):
        note = self._existing()
        snapshot = self._snapshot()
        result = self.reads.get_note_markdown(note.id, modification_allowed=True)
        self.assertEqual(result["status"], "ok")
        self.assertEqual(result["markdown"], note.content_markdown)
        self.assertEqual(result["version"], self._token(note))
        self.assertEqual(self._snapshot(), snapshot)


if __name__ == "__main__":
    unittest.main()
