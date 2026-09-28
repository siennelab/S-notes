"""Step 2C: actual Milkdown writes against fictional temporary Notes only."""
from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QApplication

from src.snotes.application.ai_access_metadata_service import AiAccessMetadataService
from src.snotes.application.ai_acceptance_outcome import AcceptanceKind
from src.snotes.application.ai_audit_service import AiAuditService
from src.snotes.application.ai_receipt_inbox_service import (
    AiReceiptAcceptFields, AiReceiptInboxService,
)
from src.snotes.application.ai_safe_read_service import AiSafeReadService
from src.snotes.application.ai_write_authorization import AiWriteAuthorization
from src.snotes.application.directory_service import DirectoryService
from src.snotes.application.local_mcp_write_service import LocalMcpWriteService, note_version_token
from src.snotes.application.note_canonical_candidate import build_canonical_note_candidate
from src.snotes.application.note_service import NoteService
from src.snotes.application.tag_service import TagService
from src.snotes.domain.models import NoteLocation
from src.snotes.storage.repositories.ai_audit import SQLiteAiAuditRepository
from src.snotes.storage.repositories.ai_mcp_operations import SQLiteAiMcpOperationRepository
from src.snotes.storage.repositories.ai_receipts import SQLiteAiReceiptInboxRepository
from src.snotes.storage.repositories.directories import SQLiteDirectoriesRepository
from src.snotes.storage.repositories.notes import SQLiteNotesRepository
from src.snotes.storage.repositories.tags import SQLiteTagsRepository
from src.snotes.storage.schema import initialize_database
from src.snotes.ui.ai_center_dialog import ReceiptCard
from src.snotes.ui.markdown_canonicalizer import CanonicalizationError, MarkdownCanonicalizer
from tests.canonicalizer_test_double import CanonicalizerTestDouble


class CanonicalWriteTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.home = tempfile.TemporaryDirectory(prefix="snotes-2c-home-")
        cls.environment = patch.dict(os.environ, {
            "HOME": cls.home.name, "S_NOTES_DATA_ROOT": cls.home.name,
            "QT_QPA_PLATFORM": "offscreen", "QTWEBENGINE_CHROMIUM_FLAGS": "--disable-gpu",
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
        self.temporary = tempfile.TemporaryDirectory(prefix="snotes-2c-db-")
        self.addCleanup(self.temporary.cleanup)
        root = Path(self.temporary.name)
        self.connections = initialize_database(root / "fixture.db")
        directories = SQLiteDirectoriesRepository(self.connections)
        tags_repository = SQLiteTagsRepository(self.connections)
        notes_repository = SQLiteNotesRepository(self.connections, directories)
        self.notes = NoteService(notes_repository)
        self.tags = TagService(tags_repository)
        self.metadata = AiAccessMetadataService(notes_repository)
        self.directories = DirectoryService(directories)
        self.canonicalizer = CanonicalizerTestDouble(
            lambda markdown: self._canonicalize(markdown)
        )
        self.receipts = AiReceiptInboxService(
            SQLiteAiReceiptInboxRepository(self.connections, directories, tags_repository),
            notes=self.notes, tags=self.tags, ai_metadata=self.metadata,
            mcp_operations=SQLiteAiMcpOperationRepository(self.connections, tags_repository),
            canonicalizer=self.canonicalizer,
            authorization=AiWriteAuthorization(directories=self.directories),
        )
        self.writes = LocalMcpWriteService(
            actor_target_id="clawbot", notes=self.notes,
            directories=self.directories, tags=self.tags,
            ai_metadata=self.metadata, safe_reads=AiSafeReadService(root / "unused-rules.json"),
            audit=AiAuditService(SQLiteAiAuditRepository(self.connections)), receipt_inbox=self.receipts,
        )
        self._confirmation(False)

    def _confirmation(self, required, operation="modify"):
        with self.connections.write() as connection:
            connection.execute(
                "INSERT OR REPLACE INTO app_settings(setting_key, setting_value, updated_at) VALUES (?, ?, ?)",
                (f"ai_{operation}_requires_confirmation", str(int(required)), "2026-01-01T00:00:00"),
            )

    def _note(self, body):
        note = self.notes.create_note(NoteLocation())
        note = self.notes.save_note(note.id, title="Fictional", content_markdown=body,
                                    location=note.location, expected_revision=note.revision)
        self.metadata.set_note_visibility(note.id, "full")
        self.metadata.set_note_targets(note.id, ("clawbot",))
        return self.notes.get_note(note.id)

    def _modify(self, note, operation="append", content="- new", request_id="modify"):
        return getattr(self.writes, f"{operation}_note")(
            request_id=request_id, note_id=note.id, content=content,
            expected_version=note_version_token(note.id, note.updated_at),
        )

    def _accept(self, proposal_id, body=None):
        receipt = self.receipts.get_receipt(proposal_id)
        return self.receipts.accept_receipt(proposal_id, AiReceiptAcceptFields(
            title=receipt.title, body=receipt.body if body is None else body,
            main_category=receipt.main_category, sub_category=receipt.sub_category,
            tags=receipt.tags, visibility=receipt.ai_visibility, target_ids=("clawbot",),
        ))

    def _fixed(self, body):
        with MarkdownCanonicalizer() as milkdown:
            self.assertEqual(milkdown._serialize_once(body), body)
            self.assertEqual(milkdown.canonicalize_markdown(body), body)

    @staticmethod
    def _canonicalize(markdown):
        with MarkdownCanonicalizer() as milkdown:
            return milkdown.canonicalize_markdown(markdown)

    def _formal_state(self):
        with self.connections.read() as connection:
            return tuple(tuple(row) for row in connection.execute("SELECT * FROM notes ORDER BY id"))

    def test_direct_create_fixed_point_and_idempotency(self):
        result = self.writes.create_note("create", "Fictional", "- one\n- two")
        self.assertEqual(result["status"], "created")
        body = self.notes.get_note(result["note_id"]).content_markdown
        self.assertEqual(body, "* one\n* two\n")
        self._fixed(body)
        before = self._formal_state()
        duplicate = self.writes.create_note("create", "Ignore", "different")
        self.assertEqual(duplicate["status"], "already_processed")
        self.assertEqual(before, self._formal_state())

    def test_canonical_direct_append_and_prepend(self):
        for operation in ("append", "prepend"):
            with self.subTest(operation=operation):
                note = self._note("base\n")
                result = self._modify(note, operation, "next", request_id=operation)
                self.assertEqual(result["status"], "updated")
                after = self.notes.get_note(note.id)
                expected = "base\n\nnext\n" if operation == "append" else "next\n\nbase\n"
                self.assertEqual(after.content_markdown, expected)
                self.assertGreater(after.revision, note.revision)
                self.assertNotEqual(after.updated_at, note.updated_at)
                self._fixed(after.content_markdown)

    def test_legacy_forced_pending_and_body_preview_accept_match(self):
        for operation in ("append", "prepend"):
            with self.subTest(operation=operation):
                note = self._note("- old")
                result = self._modify(note, operation, request_id=operation)
                self.assertEqual(result["status"], "pending_confirmation")
                self.assertEqual(self.notes.get_note(note.id), note)
                preview = self.receipts.build_pending_note_preview(result["proposal_id"])
                self.assertEqual(preview.before_markdown, "- old")
                self.assertTrue(preview.legacy_canonicalization_changed)
                self.assertIn("* old", preview.final_markdown)
                self.assertIn("* new", preview.final_markdown)
                self.assertEqual(self.notes.get_note(note.id), note)
                accepted = self._accept(result["proposal_id"])
                self.assertEqual(accepted.note.content_markdown, preview.final_markdown)
                self._fixed(accepted.note.content_markdown)

    def test_canonical_pending_accept_uses_edited_fragment_and_shared_builder(self):
        self._confirmation(True)
        note = self._note("base\n")
        result = self._modify(note)
        self.assertEqual(result["status"], "pending_confirmation")
        preview = self.receipts.build_pending_note_preview(result["proposal_id"], addition_markdown="- edited")
        expected = build_canonical_note_candidate(
            note.content_markdown,
            "- edited",
            canonicalize=self.canonicalizer.canonicalize_markdown,
        )
        self.assertEqual(preview.final_markdown, expected.final_markdown)
        accepted = self._accept(result["proposal_id"], "- edited")
        self.assertEqual(accepted.note.content_markdown, expected.final_markdown)
        self._fixed(accepted.note.content_markdown)

    def test_stale_accept_zero_write(self):
        note = self._note("- old")
        result = self._modify(note)
        self.notes.save_note(note.id, title=note.title, content_markdown="changed\n",
                             location=note.location, expected_revision=note.revision)
        before = self._formal_state()
        accepted = self._accept(result["proposal_id"])
        self.assertEqual(accepted.outcome.kind, AcceptanceKind.CONFLICT)
        self.assertEqual(self._formal_state(), before)

    def test_pending_create_accept_canonicalized(self):
        self._confirmation(True, "create")
        result = self.writes.create_note("create", "Fictional", "- proposed")
        self.assertEqual(result["status"], "pending_confirmation")
        self.assertEqual(self._formal_state(), ())
        accepted = self._accept(result["proposal_id"], "- edited")
        self.assertEqual(accepted.note.content_markdown, "* edited\n")
        self._fixed(accepted.note.content_markdown)

    def test_canonicalizer_failure_zero_formal_write(self):
        note = self._note("base\n")
        self._confirmation(True)
        pending = self._modify(note, request_id="pending")
        self._confirmation(False)
        self._confirmation(True, "create")
        pending_create = self.writes.create_note("pending-create", "Fictional", "- input")
        self._confirmation(False, "create")
        before = self._formal_state()
        with patch.object(
            self.canonicalizer,
            "canonicalize",
            side_effect=CanonicalizationError("fixture failure"),
        ):
            create = self.writes.create_note("failed-create", "Fictional", "- input")
            self.assertIn(create["status"], {"failed", "rejected", "pending_confirmation"})
            for operation in ("append", "prepend"):
                result = self._modify(note, operation, request_id="failed-" + operation)
                self.assertEqual(result["status"], "pending_confirmation")
            rejected = self._accept(pending["proposal_id"])
            rejected_create = self._accept(pending_create["proposal_id"])
            self.assertEqual(rejected.outcome.kind, AcceptanceKind.REJECTED)
            self.assertEqual(rejected_create.outcome.kind, AcceptanceKind.REJECTED)
        self.assertEqual(self._formal_state(), before)

    def test_metadata_only_skips_body_canonicalization(self):
        note = self._note("- legacy")
        with patch.object(
            self.canonicalizer,
            "canonicalize",
            side_effect=AssertionError("metadata invoked canonicalizer"),
        ):
            result = self.writes.update_note_metadata(
                request_id="metadata", note_id=note.id,
                expected_version=note_version_token(note.id, note.updated_at), title="New title",
            )
        self.assertEqual(result["status"], "updated")
        self.assertEqual(self.notes.get_note(note.id).content_markdown, note.content_markdown)

    def test_save_during_canonicalization_blocks_direct_and_accept(self):
        original = self.canonicalizer.canonicalize
        for mode in ("direct", "accept"):
            with self.subTest(mode=mode):
                note = self._note("base\n")
                if mode == "accept":
                    self._confirmation(True)
                    pending = self._modify(note, request_id="pending-race")
                    self._confirmation(False)
                changed = []

                def save_during_canonicalization(markdown):
                    if not changed:
                        changed.append(self.notes.save_note(
                            note.id, title=note.title, content_markdown="concurrent save\n",
                            location=note.location, expected_revision=note.revision,
                        ))
                    return original(markdown)

                with patch.object(
                    self.canonicalizer,
                    "canonicalize",
                    side_effect=save_during_canonicalization,
                ):
                    if mode == "direct":
                        result = self._modify(note, request_id="direct-race")
                        self.assertEqual(result["status"], "conflict")
                    else:
                        result = self._accept(pending["proposal_id"])
                        self.assertEqual(result.outcome.kind, AcceptanceKind.CONFLICT)
                self.assertEqual(self.notes.get_note(note.id), changed[0])

    def test_append_ui_requires_current_successful_full_preview(self):
        note = self._note("- old")
        pending = self._modify(note)
        card = ReceiptCard(self.receipts.get_receipt(pending["proposal_id"]), self.receipts, self.metadata)
        self.addCleanup(card.close)
        card.show()
        self.assertFalse(card.accept_button.isEnabled())
        card.set_expanded(True)
        card.refresh_final_preview()
        self.assertTrue(card.accept_button.isEnabled())
        self.assertTrue(card.legacy_warning.isVisible())
        self.assertIn("+* old", card.final_preview.toPlainText())
        card.body_edit.setPlainText("- edited")
        self.assertFalse(card.accept_button.isEnabled())
        with patch.object(
            self.canonicalizer,
            "canonicalize",
            side_effect=CanonicalizationError("fixture failure"),
        ):
            card.refresh_final_preview()
        self.assertFalse(card.accept_button.isEnabled())
        card.refresh_final_preview()
        self.assertTrue(card.accept_button.isEnabled())
        self.assertEqual(self.notes.get_note(note.id), note)


class StdioCanonicalWriteTests(unittest.IsolatedAsyncioTestCase):
    """Exercise the real MCP stdio process, not an in-process transport mock."""
    from tests.test_package5_local_mcp_protocol import Package5LocalMcpProtocolTests as _Fixture
    setUp = _Fixture.setUp
    _seed_note = _Fixture._seed_note
    _seed_fixture = _Fixture._seed_fixture
    session = _Fixture.session
    call = _Fixture.call

    def _env(self, **kwargs):
        values = self._Fixture._env(self, **kwargs)
        values.update(PATH="/usr/bin:/bin", QT_QPA_PLATFORM="offscreen",
                      QTWEBENGINE_CHROMIUM_FLAGS="--disable-gpu")
        return values

    async def test_stdio_no_node_create_modify_and_legacy_pending(self):
        with self.connections.write() as connection:
            connection.execute(
                "INSERT OR REPLACE INTO app_settings(setting_key, setting_value, updated_at) VALUES (?, ?, ?)",
                ("ai_modify_requires_confirmation", "0", "2026-01-01T00:00:00"),
            )
        legacy = self.notes.get_note(self.ids["common"])
        async with self.session(read_only=False, actor="claude-code") as session:
            created = await self.call(session, "create_note", {
                "request_id": "2c-stdio-create", "title": "Fictional", "content": "- first",
            })
            self.assertEqual(created["status"], "created")
            note = await self.call(session, "get_note_markdown", {"note_id": created["note_id"]})
            self.assertEqual(note["markdown"], "* first\n")
            for operation, fragment in (("append", "next"), ("prepend", "before")):
                modified = await self.call(session, operation + "_note", {
                    "request_id": "2c-stdio-" + operation, "note_id": created["note_id"],
                    "content": fragment, "expected_version": note["version"],
                })
                self.assertEqual(modified["status"], "updated")
                note = await self.call(session, "get_note_markdown", {"note_id": created["note_id"]})
            self.assertEqual(note["markdown"], "before\n\n* first\n\nnext\n")
            for operation in ("append", "prepend"):
                pending = await self.call(session, operation + "_note", {
                    "request_id": "2c-stdio-legacy-" + operation, "note_id": legacy.id,
                    "content": "new", "expected_version": note_version_token(legacy.id, legacy.updated_at),
                })
                self.assertEqual(pending["status"], "pending_confirmation")
                self.assertEqual(self.notes.get_note(legacy.id), legacy)
        with MarkdownCanonicalizer() as milkdown:
            self.assertEqual(milkdown._serialize_once(note["markdown"]), note["markdown"])


if __name__ == "__main__":
    unittest.main()
