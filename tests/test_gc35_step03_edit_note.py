"""Step 3 edit_note contracts against fictional Notes and bundled Milkdown."""

from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from PySide6.QtCore import Qt, QTimer
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QMessageBox

from src.snotes.application.ai_access_metadata_service import AiAccessMetadataService
from src.snotes.application.ai_audit_service import AiAuditService
from src.snotes.application.ai_receipt_inbox_service import AiReceiptAcceptFields, AiReceiptInboxService
from src.snotes.application.ai_safe_read_service import AiSafeReadService
from src.snotes.application.directory_service import DirectoryService
from src.snotes.application.local_mcp_write_service import LocalMcpWriteService, note_version_token
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
from src.snotes.ui.ai_center_dialog import ReceiptInboxView
from src.snotes.ui.markdown_canonicalizer import CanonicalizationError, MarkdownCanonicalizer
from tests.canonicalizer_test_double import CanonicalizerTestDouble


class EditNoteTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.home = tempfile.TemporaryDirectory(prefix="snotes-step03-home-")
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
        temporary = tempfile.TemporaryDirectory(prefix="snotes-step03-db-")
        self.addCleanup(temporary.cleanup)
        root = Path(temporary.name)
        (root / "redaction-rules.json").write_text(
            json.dumps({"replacements": [{"from": "Before", "to": "[hidden]"}]}),
            encoding="utf-8",
        )
        self.connections = initialize_database(root / "fixture.db")
        directories = SQLiteDirectoriesRepository(self.connections)
        tags_repository = SQLiteTagsRepository(self.connections)
        notes_repository = SQLiteNotesRepository(self.connections, directories)
        self.notes = NoteService(notes_repository)
        self.tags = TagService(tags_repository)
        self.metadata = AiAccessMetadataService(notes_repository)
        self.canonicalizer = CanonicalizerTestDouble(
            lambda markdown: self._canonicalize(markdown)
        )
        self.receipts = AiReceiptInboxService(
            SQLiteAiReceiptInboxRepository(self.connections, directories, tags_repository),
            notes=self.notes,
            tags=self.tags,
            ai_metadata=self.metadata,
            mcp_operations=SQLiteAiMcpOperationRepository(self.connections, tags_repository),
            canonicalizer=self.canonicalizer,
        )
        self.writes = LocalMcpWriteService(
            actor_target_id="clawbot",
            notes=self.notes,
            directories=DirectoryService(directories),
            tags=self.tags,
            ai_metadata=self.metadata,
            safe_reads=AiSafeReadService(root / "redaction-rules.json"),
            audit=AiAuditService(SQLiteAiAuditRepository(self.connections)),
            receipt_inbox=self.receipts,
        )
        self._confirmation(True)

    def _confirmation(self, required: bool):
        with self.connections.write() as connection:
            connection.execute(
                "INSERT OR REPLACE INTO app_settings(setting_key, setting_value, updated_at) VALUES (?, ?, ?)",
                ("ai_modify_requires_confirmation", str(int(required)), "2026-01-01T00:00:00"),
            )

    def _note(self, body: str):
        created = self.notes.create_note(NoteLocation())
        saved = self.notes.save_note(
            created.id, title="Fictional", content_markdown=body,
            location=created.location, expected_revision=created.revision,
        )
        self.metadata.set_note_visibility(saved.id, "full")
        self.metadata.set_note_targets(saved.id, ("clawbot",))
        return self.notes.get_note(saved.id)

    def _edit(self, note, edits, *, request_id="edit", expected_version=None):
        return self.writes.edit_note(
            request_id=request_id,
            note_id=note.id,
            expected_version=expected_version if expected_version is not None else self._version(note),
            edits=edits,
        )

    @staticmethod
    def _version(note):
        return note_version_token(note.id, note.updated_at)

    def _accept(self, proposal_id):
        receipt = self.receipts.get_receipt(proposal_id)
        return self.receipts.accept_receipt(proposal_id, AiReceiptAcceptFields(
            title=receipt.title, body=receipt.body,
            main_category=receipt.main_category, sub_category=receipt.sub_category,
            tags=receipt.tags, visibility=receipt.ai_visibility,
            target_ids=("clawbot",),
        ))

    def _formal_state(self):
        with self.connections.read() as connection:
            return tuple(tuple(row) for row in connection.execute("SELECT * FROM notes ORDER BY id"))

    def _assert_fixed_point(self, body):
        with MarkdownCanonicalizer() as milkdown:
            self.assertEqual(milkdown._serialize_once(body), body)
            self.assertEqual(milkdown.canonicalize_markdown(body), body)

    @staticmethod
    def _canonicalize(markdown):
        with MarkdownCanonicalizer() as milkdown:
            return milkdown.canonicalize_markdown(markdown)

    def test_conflict_outbound_query_and_retry_semantics(self):
        from src.snotes.transport.mcp.tools import _operation_response, _write_response
        note = self._note("Before\n")
        edits = [{"old_markdown": "Before", "new_markdown": "After"}]
        pending = self._edit(note, edits)
        self.notes.save_note(note.id, title=note.title, content_markdown="S edited\n",
                             location=note.location, expected_revision=note.revision)
        with self.assertRaisesRegex(ValueError, "笔记已被更新"):
            self._accept(pending["proposal_id"])
        stored = self.writes.get_operation_status("edit")
        self.assertEqual(stored["handling_mode"], "human_rejected")
        query = _operation_response(stored)
        self.assertEqual(query.status, "conflict")
        self.assertEqual(query.processing_mode, "none")
        self.assertTrue(query.failure_or_conflict_reason)
        retry = _write_response(self._edit(note, edits))
        self.assertEqual((retry.status, retry.previous_status, retry.processing_mode),
                         ("already_processed", "conflict", "none"))
        self.assertFalse(retry.changed_formal_note)
        self.assertEqual((retry.note_id, retry.proposal_id), (note.id, pending["proposal_id"]))

    def test_rejected_edit_outbound_preserves_linkage(self):
        from src.snotes.transport.mcp.tools import _operation_response, _write_response
        note = self._note("Before\n")
        edits = [{"old_markdown": "Before", "new_markdown": "After"}]
        pending = self._edit(note, edits)
        self.receipts.discard_receipt(pending["proposal_id"])
        for response in (_operation_response(self.writes.get_operation_status("edit")),
                         _write_response(self._edit(note, edits))):
            self.assertEqual(response.processing_mode, "human_rejected")
            self.assertEqual((response.note_id, response.proposal_id),
                             (note.id, pending["proposal_id"]))

    def test_rejected_create_preserves_proposal_without_formal_note(self):
        from src.snotes.transport.mcp.tools import _operation_response, _write_response
        with patch.object(self.receipts, "mcp_confirmation_required", return_value=True):
            pending = self.writes.create_note("create-rejected", "Fictional", "Before")
        self.assertEqual(pending["status"], "pending_confirmation")
        self.receipts.discard_receipt(pending["proposal_id"])
        for response in (_operation_response(self.writes.get_operation_status("create-rejected")),
                         _write_response(self.writes.create_note("create-rejected", "Fictional", "Before"))):
            self.assertIsNone(response.note_id)
            self.assertEqual(response.proposal_id, pending["proposal_id"])
            self.assertEqual(response.processing_mode, "human_rejected")

    def test_direct_edits_common_milkdown_constructs_and_updates_version(self):
        self._confirmation(False)
        cases = (
            ("ordinary", "Before\n", "Before", "After"),
            ("bold", "**Before**\n", "Before", "After"),
            ("link", "[Before](https://example.test)\n", "Before", "After"),
            ("table", "| Name | Value |\n| - | - |\n| Before | one |\n", "Before", "After"),
            ("size15", ':size[Before]{size="15"}\n', "Before", "After"),
            ("size20", ':size[Before]{size="20"}\n', "Before", "After"),
        )
        with MarkdownCanonicalizer() as milkdown:
            for name, initial, old, new in cases:
                with self.subTest(name=name):
                    initial = milkdown.canonicalize_markdown(initial)
                    before = self._note(initial)
                    expected = milkdown.canonicalize_markdown(initial.replace(old, new))
                    result = self._edit(
                        before, [{"old_markdown": old, "new_markdown": new}],
                        request_id=f"direct-{name}",
                    )
                    self.assertEqual(result["status"], "updated", result)
                    after = self.notes.get_note(before.id)
                    self.assertEqual(after.content_markdown, expected)
                    self.assertEqual(after.revision, before.revision + 1)
                    self.assertNotEqual(after.updated_at, before.updated_at)
                    self.assertNotEqual(self._version(after), self._version(before))
                    self.assertEqual(milkdown._serialize_once(after.content_markdown), after.content_markdown)

    def test_pending_batch_accepts_atomically_with_version_and_history(self):
        before = self._note("Alpha\n\n**Bold** and [Link](https://example.test)\n")
        edits = [
            {"old_markdown": "Alpha", "new_markdown": "Omega"},
            {"old_markdown": "Bold", "new_markdown": "Strong"},
            {"old_markdown": "Link", "new_markdown": "Source"},
        ]
        pending = self._edit(before, edits, request_id="batch-pending")
        self.assertEqual(pending["status"], "pending_confirmation", pending)
        self.assertEqual(self.notes.get_note(before.id), before)
        accepted = self._accept(pending["proposal_id"])
        self.assertEqual(accepted.receipt.status, "accepted")
        after = accepted.note
        self.assertEqual(after.content_markdown, "Omega\n\n**Strong**&#32;and&#32;[Source](https://example.test)\n")
        self.assertEqual(after.revision, before.revision + 1)
        self.assertNotEqual(after.updated_at, before.updated_at)
        self.assertNotEqual(self._version(after), self._version(before))
        with self.connections.read() as connection:
            history = connection.execute(
                "SELECT COUNT(*) FROM ai_note_history WHERE note_id = ? AND modification_type = 'edit'",
                (before.id,),
            ).fetchone()[0]
        self.assertGreaterEqual(history, 1)
        self._assert_fixed_point(after.content_markdown)

    def test_zero_multiple_and_overlapping_matches_reject_whole_batch(self):
        self._confirmation(False)
        before = self._note("Alpha\n\nRepeat and Repeat\n")
        initial_state = self._formal_state()
        bad_batches = (
            [{"old_markdown": "Missing", "new_markdown": "New"}],
            [{"old_markdown": "Repeat", "new_markdown": "New"}],
            [{"old_markdown": before.content_markdown, "new_markdown": "Whole rewrite\n"}],
            [{"old_markdown": "Alpha", "new_markdown": ""},
             {"old_markdown": "Repeat and Repeat", "new_markdown": ""}],
            [
                {"old_markdown": "Alpha", "new_markdown": "Omega"},
                {"old_markdown": "Missing", "new_markdown": "New"},
            ],
            [
                {"old_markdown": "Repeat and Repeat", "new_markdown": "New"},
                {"old_markdown": "and", "new_markdown": "plus"},
            ],
        )
        for index, edits in enumerate(bad_batches):
            with self.subTest(index=index):
                result = self._edit(before, edits, request_id=f"bad-{index}")
                self.assertEqual(result["status"], "rejected", result)
                self.assertEqual(self._formal_state(), initial_state)

    def test_overlapping_occurrences_are_multiple_matches(self):
        self._confirmation(False)
        before = self._note("aaa\n")
        state = self._formal_state()
        result = self._edit(
            before, [{"old_markdown": "aa", "new_markdown": "b"}],
            request_id="overlapping-occurrences",
        )
        self.assertEqual(result["status"], "rejected", result)
        self.assertEqual(self._formal_state(), state)

    def test_each_edit_matches_original_body_not_prior_replacement(self):
        self._confirmation(False)
        before = self._note("Alpha\n\nBeta\n")
        result = self._edit(before, [
            {"old_markdown": "Alpha", "new_markdown": "Beta"},
            {"old_markdown": "Beta", "new_markdown": "Gamma"},
        ], request_id="original-body-matches")
        self.assertEqual(result["status"], "updated", result)
        self.assertEqual(self.notes.get_note(before.id).content_markdown, "Beta\n\nGamma\n")

    def test_raw_html_edit_is_rejected_without_changing_body(self):
        self._confirmation(False)
        before = self._note("Before\n")
        state = self._formal_state()
        result = self._edit(
            before, [{"old_markdown": "Before", "new_markdown": "<div>After</div>"}],
            request_id="unsupported-html",
        )
        self.assertEqual(result["status"], "rejected", result)
        self.assertEqual(self._formal_state(), state)

    def test_stale_version_and_stale_pending_accept_leave_formal_note_unchanged(self):
        before = self._note("Before\n")
        edits = [{"old_markdown": "Before", "new_markdown": "After"}]
        stale = self._edit(before, edits, request_id="stale", expected_version="snotes-v1-stale")
        self.assertEqual(stale["status"], "conflict", stale)
        pending = self._edit(before, edits, request_id="pending-stale")
        self.assertEqual(pending["status"], "pending_confirmation", pending)
        saved = self.notes.save_note(
            before.id, title=before.title, content_markdown="S edited\n",
            location=before.location, expected_revision=before.revision,
        )
        formal_state = self._formal_state()
        with self.assertRaisesRegex(ValueError, "笔记已被更新"):
            self._accept(pending["proposal_id"])
        self.assertEqual(self._formal_state(), formal_state)
        self.assertEqual(self.notes.get_note(before.id), saved)

    def test_pending_accept_rechecks_actor_full_permission(self):
        before = self._note("Before\n")
        pending = self._edit(
            before, [{"old_markdown": "Before", "new_markdown": "After"}],
            request_id="pending-revoked",
        )
        self.assertEqual(pending["status"], "pending_confirmation", pending)
        self.metadata.set_note_targets(before.id, ())
        formal_state = self._formal_state()
        with self.assertRaises(ValueError):
            self._accept(pending["proposal_id"])
        self.assertEqual(self._formal_state(), formal_state)
        self.assertEqual(self.notes.get_note(before.id).content_markdown, before.content_markdown)

    def test_accept_rechecks_unique_match_even_if_body_changes_without_version(self):
        before = self._note("Alpha\n")
        pending = self._edit(
            before, [{"old_markdown": "Alpha", "new_markdown": "Beta"}],
            request_id="accept-match-recheck",
        )
        self.assertEqual(pending["status"], "pending_confirmation", pending)
        # Simulate an external writer that incorrectly kept the version token.
        # Acceptance must still reject a now ambiguous old_markdown.
        with self.connections.write() as connection:
            connection.execute(
                "UPDATE notes SET content_markdown = ? WHERE id = ?",
                ("Alpha Alpha\n", before.id),
            )
        formal_state = self._formal_state()
        with self.assertRaises(ValueError):
            self._accept(pending["proposal_id"])
        self.assertEqual(self._formal_state(), formal_state)
        self.assertEqual(self.receipts.get_receipt(pending["proposal_id"]).status, "rejected")

    def test_accept_canonicalizer_failure_rolls_back_note_and_history(self):
        before = self._note("Before\n")
        pending = self._edit(
            before, [{"old_markdown": "Before", "new_markdown": "After"}],
            request_id="accept-canonicalizer-failed",
        )
        self.assertEqual(pending["status"], "pending_confirmation", pending)
        formal_state = self._formal_state()
        with self.connections.read() as connection:
            before_history = connection.execute(
                "SELECT COUNT(*) FROM ai_note_history WHERE note_id = ?", (before.id,),
            ).fetchone()[0]
        with patch.object(
            self.canonicalizer,
            "canonicalize",
            side_effect=CanonicalizationError("fixture failure"),
        ):
            with self.assertRaises(ValueError):
                self._accept(pending["proposal_id"])
        self.assertEqual(self._formal_state(), formal_state)
        with self.connections.read() as connection:
            after_history = connection.execute(
                "SELECT COUNT(*) FROM ai_note_history WHERE note_id = ?", (before.id,),
            ).fetchone()[0]
        self.assertEqual(after_history, before_history)
        self.assertEqual(self.receipts.get_receipt(pending["proposal_id"]).status, "rejected")

    def test_redacted_and_hidden_notes_have_no_edit_capability(self):
        before = self._note("Before\n")
        for visibility in ("redacted", "hidden"):
            with self.subTest(visibility=visibility):
                self.metadata.set_note_visibility(before.id, visibility)
                formal_state = self._formal_state()
                result = self._edit(
                    before, [{"old_markdown": "Before", "new_markdown": "After"}],
                    request_id=f"visibility-{visibility}",
                )
                self.assertIn(result["status"], {"not_found", "rejected"}, result)
                self.assertEqual(self._formal_state(), formal_state)

    def test_deleted_note_has_no_edit_capability(self):
        before = self._note("Before\n")
        self.notes.soft_delete_note(before.id)
        state = self._formal_state()
        result = self._edit(
            before, [{"old_markdown": "Before", "new_markdown": "After"}],
            request_id="deleted",
        )
        self.assertIn(result["status"], {"not_found", "rejected"}, result)
        self.assertEqual(self._formal_state(), state)

    def test_duplicate_request_does_not_apply_twice(self):
        self._confirmation(False)
        before = self._note("Before\n")
        edits = [{"old_markdown": "Before", "new_markdown": "After"}]
        first = self._edit(before, edits, request_id="duplicate")
        self.assertEqual(first["status"], "updated", first)
        formal_state = self._formal_state()
        duplicate = self._edit(before, edits, request_id="duplicate")
        self.assertEqual(duplicate["status"], "already_processed", duplicate)
        self.assertEqual(self._formal_state(), formal_state)

    def test_ai_center_shows_each_exact_edit_and_accepts_final_preview(self):
        before = self._note("First\n\nSecond\n")
        pending = self._edit(before, [
            {"old_markdown": "First", "new_markdown": "One"},
            {"old_markdown": "Second", "new_markdown": "Two"},
        ], request_id="ui-edit")
        self.assertEqual(pending["status"], "pending_confirmation", pending)
        receipt_id = pending["proposal_id"]
        view = ReceiptInboxView(self.receipts, self.metadata)
        self.addCleanup(view.close)
        view.resize(1000, 800)
        view.show()
        view.toggle_card(receipt_id)
        card = view.cards[receipt_id]
        self.assertTrue(card.is_edit_proposal)
        self.assertFalse(card.accept_button.isEnabled())
        card.refresh_final_preview()
        self.assertTrue(card.final_preview_ready)
        self.assertIn("First", card.edit_diff.toPlainText())
        self.assertIn("One", card.edit_diff.toPlainText())
        self.assertIn("Second", card.edit_diff.toPlainText())
        self.assertIn("Two", card.edit_diff.toPlainText())
        self.assertIn("+One", card.final_preview.toPlainText())
        self.assertFalse(card.body_edit.isEnabled())
        with patch.object(QMessageBox, "warning", side_effect=AssertionError("accept failed")):
            QTest.mouseClick(card.accept_button, Qt.LeftButton)
        self.assertEqual(self.receipts.get_receipt(receipt_id).status, "accepted")
        self.assertEqual(self.notes.get_note(before.id).content_markdown, "One\n\nTwo\n")
        self.assertNotIn(receipt_id, view.cards)

    def test_unknown_directive_is_preserved_and_canonicalizer_failure_never_writes(self):
        self._confirmation(False)
        with MarkdownCanonicalizer() as milkdown:
            body = milkdown.canonicalize_markdown(':future[Keep]{mode="x"}\n\nBefore')
        before = self._note(body)
        result = self._edit(
            before, [{"old_markdown": "Before", "new_markdown": "After"}],
            request_id="directive",
        )
        self.assertEqual(result["status"], "updated", result)
        after = self.notes.get_note(before.id)
        self.assertIn(body.split("\n", 1)[0], after.content_markdown)
        self._assert_fixed_point(after.content_markdown)

        state = self._formal_state()
        with patch.object(
            self.canonicalizer,
            "canonicalize",
            side_effect=CanonicalizationError("fixture failure"),
        ):
            failed = self._edit(
                after, [{"old_markdown": "After", "new_markdown": "Later"}],
                request_id="canonicalizer-failed",
            )
        self.assertIn(failed["status"], {"failed", "rejected", "pending_confirmation"}, failed)
        self.assertEqual(self._formal_state(), state)


if __name__ == "__main__":
    unittest.main()
