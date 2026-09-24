"""Step 2E pending acceptance against disposable Notes and bundled Milkdown."""

from __future__ import annotations

import json
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
from src.snotes.ui.markdown_canonicalizer import CanonicalizationError, MarkdownCanonicalizer


class Step02EAcceptanceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.home = tempfile.TemporaryDirectory(prefix="snotes-2e-home-")
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
        temporary = tempfile.TemporaryDirectory(prefix="snotes-2e-db-")
        self.addCleanup(temporary.cleanup)
        root = Path(temporary.name)
        (root / "redaction-rules.json").write_text(
            json.dumps({"replacements": [{"from": "FixtureSecret", "to": "[redacted]"}]}),
            encoding="utf-8",
        )
        self.connections = initialize_database(root / "fixture.db")
        directory_repository = SQLiteDirectoriesRepository(self.connections)
        tags_repository = SQLiteTagsRepository(self.connections)
        notes_repository = SQLiteNotesRepository(self.connections, directory_repository)
        self.notes = NoteService(notes_repository)
        self.tags = TagService(tags_repository)
        self.metadata = AiAccessMetadataService(notes_repository)
        self.receipts = AiReceiptInboxService(
            SQLiteAiReceiptInboxRepository(self.connections, directory_repository, tags_repository),
            notes=self.notes,
            tags=self.tags,
            ai_metadata=self.metadata,
            mcp_operations=SQLiteAiMcpOperationRepository(self.connections, tags_repository),
        )
        self.writes = LocalMcpWriteService(
            actor_target_id="clawbot",
            notes=self.notes,
            directories=DirectoryService(directory_repository),
            tags=self.tags,
            ai_metadata=self.metadata,
            safe_reads=AiSafeReadService(root / "redaction-rules.json"),
            audit=AiAuditService(SQLiteAiAuditRepository(self.connections)),
            receipt_inbox=self.receipts,
        )
        with self.connections.write() as connection:
            connection.execute(
                "INSERT OR REPLACE INTO app_settings(setting_key, setting_value, updated_at) VALUES (?, ?, ?)",
                ("ai_modify_requires_confirmation", "1", "2026-01-01T00:00:00"),
            )

    @staticmethod
    def _token(note):
        return note_version_token(note.id, note.updated_at)

    def _note(self, body="base\n", *, location=None, title="Fictional", tags=()):
        blank = self.notes.create_note(location or NoteLocation())
        saved = self.notes.save_note(
            blank.id,
            title=title,
            content_markdown=body,
            location=blank.location,
            expected_revision=blank.revision,
        )
        if tags:
            self.tags.set_note_tags(saved.id, tags)
        self.metadata.set_note_visibility(saved.id, "full")
        self.metadata.set_note_targets(saved.id, ("clawbot",))
        return self.notes.get_note(saved.id)

    def _pending(self, note, operation, request_id, **values):
        arguments = dict(
            request_id=request_id,
            note_id=note.id,
            expected_version=self._token(note),
        )
        arguments.update(values)
        result = getattr(self.writes, f"{operation}_note" if operation != "metadata" else "update_note_metadata")(**arguments)
        self.assertEqual(result["status"], "pending_confirmation")
        self.assertEqual(self.receipts.get_receipt(result["proposal_id"]).status, "pending")
        return result["proposal_id"]

    def _accept(self, proposal_id, *, body=None):
        receipt = self.receipts.get_receipt(proposal_id)
        return self.receipts.accept_receipt(proposal_id, AiReceiptAcceptFields(
            title=receipt.title,
            body=receipt.body if body is None else body,
            main_category=receipt.main_category,
            sub_category=receipt.sub_category,
            tags=receipt.tags,
            visibility=receipt.ai_visibility,
            target_ids=("clawbot",),
        ))

    def _note_row(self, note_id):
        with self.connections.read() as connection:
            row = connection.execute("SELECT * FROM notes WHERE id = ?", (note_id,)).fetchone()
            return tuple(row) if row is not None else None

    def _history(self, request_id):
        with self.connections.read() as connection:
            return [dict(row) for row in connection.execute(
                "SELECT * FROM ai_note_history WHERE request_id = ? ORDER BY id", (request_id,)
            )]

    def _result_status(self, proposal_id):
        with self.connections.read() as connection:
            return connection.execute(
                "SELECT result_status FROM ai_receipt_inbox WHERE id = ?", (proposal_id,)
            ).fetchone()["result_status"]

    def _assert_rejected_without_note_write(self, proposal_id, note_id, request_id, snapshot):
        with self.assertRaises(ValueError):
            self._accept(proposal_id)
        self.assertEqual(self._note_row(note_id), snapshot)
        self.assertEqual(self._history(request_id), [])
        self.assertEqual(self.receipts.get_receipt(proposal_id).status, "rejected")
        self.assertIn(self._result_status(proposal_id), {"conflict", "rejected"})
        operation = self.receipts.mcp_get_operation(request_id)
        self.assertIn(operation["status"], {"conflict", "rejected"})

    def _revoke_target_without_note_version_change(self, note):
        with self.connections.write() as connection:
            connection.execute(
                "DELETE FROM note_ai_targets WHERE note_id = ? AND target_id = ?",
                (note.id, "clawbot"),
            )
        current = self.notes.get_note(note.id)
        self.assertEqual(current.updated_at, note.updated_at)
        self.assertEqual(current.revision, note.revision)
        self.assertEqual(self._token(current), self._token(note))

    def _assert_version_advanced(self, before, after):
        self.assertEqual(after.revision, before.revision + 1)
        self.assertGreater(datetime.fromisoformat(after.updated_at), datetime.fromisoformat(before.updated_at))
        self.assertNotEqual(self._token(after), self._token(before))

    def test_prepend_legacy_preview_matches_accepted_fixed_point_and_history(self):
        before = self._note("- old")
        proposal_id = self._pending(before, "prepend", "prepend-legacy", content="- new")
        preview = self.receipts.build_pending_note_preview(proposal_id)
        self.assertTrue(preview.legacy_canonicalization_changed)
        self.assertEqual(preview.before_markdown, before.content_markdown)
        self.assertEqual(self.notes.get_note(before.id), before)

        result = self._accept(proposal_id)
        after = self.notes.get_note(before.id)
        self.assertEqual(result.note, after)
        self.assertEqual(after.content_markdown, preview.final_markdown)
        self.assertEqual(after.content_markdown, "* new\n\n* old\n")
        with MarkdownCanonicalizer() as milkdown:
            self.assertEqual(milkdown.canonicalize_markdown(after.content_markdown), after.content_markdown)
        self._assert_version_advanced(before, after)
        history = self._history("prepend-legacy")
        self.assertEqual(len(history), 1)
        self.assertEqual(history[0]["modification_type"], "prepend")
        self.assertEqual(json.loads(history[0]["before_snapshot"])["content_markdown"], before.content_markdown)
        self.assertEqual(history[0]["after_updated_at"], after.updated_at)
        self.assertEqual(result.receipt.status, "accepted")
        self.assertEqual(self._result_status(proposal_id), "updated")
        self.assertEqual(self.receipts.mcp_get_operation("prepend-legacy")["status"], "updated")

    def test_prepend_canonical_note_accepts_edited_fragment(self):
        before = self._note("base\n")
        proposal_id = self._pending(before, "prepend", "prepend-edited", content="proposed")
        preview = self.receipts.build_pending_note_preview(proposal_id, addition_markdown="edited")
        self.assertFalse(preview.legacy_canonicalization_changed)
        result = self._accept(proposal_id, body="edited")
        self.assertEqual(result.note.content_markdown, preview.final_markdown)
        self.assertEqual(result.note.content_markdown, "edited\n\nbase\n")
        self._assert_version_advanced(before, result.note)

    def test_prepend_stale_version_rejects_without_acceptance_write(self):
        before = self._note()
        proposal_id = self._pending(before, "prepend", "prepend-stale", content="new")
        self.notes.save_note(
            before.id, title=before.title, content_markdown="concurrent\n",
            location=before.location, expected_revision=before.revision,
        )
        snapshot = self._note_row(before.id)
        self._assert_rejected_without_note_write(proposal_id, before.id, "prepend-stale", snapshot)

    def test_append_and_prepend_recheck_current_permission_inside_acceptance(self):
        for operation in ("append", "prepend"):
            with self.subTest(operation=operation):
                before = self._note()
                request_id = f"revoked-{operation}"
                proposal_id = self._pending(before, operation, request_id, content="new")
                self._revoke_target_without_note_version_change(before)
                snapshot = self._note_row(before.id)
                self._assert_rejected_without_note_write(proposal_id, before.id, request_id, snapshot)

    def test_metadata_accepts_title_directory_tags_and_preserves_legacy_body(self):
        before = self._note("- legacy  \n", tags=("old",))
        destination = self._note("destination\n", location=NoteLocation("Archive"))
        proposal_id = self._pending(
            before, "metadata", "metadata-all",
            title="New title", directory_id=destination.directory_id,
            add_tags=["new"], remove_tags=["old"],
        )
        with patch.object(MarkdownCanonicalizer, "canonicalize_markdown", side_effect=AssertionError("metadata canonicalized body")):
            result = self._accept(proposal_id)
        after = self.notes.get_note(before.id)
        self.assertEqual(result.note, after)
        self.assertEqual(after.title, "New title")
        self.assertEqual(after.directory_id, destination.directory_id)
        self.assertEqual(after.location, destination.location)
        self.assertEqual(self.tags.get_note_tags(after.id), ["new"])
        self.assertEqual(after.content_markdown, before.content_markdown)
        self._assert_version_advanced(before, after)
        history = self._history("metadata-all")
        self.assertEqual(len(history), 1)
        self.assertEqual(history[0]["modification_type"], "metadata")
        self.assertEqual(history[0]["after_updated_at"], after.updated_at)
        self.assertEqual(result.receipt.status, "accepted")
        self.assertEqual(self._result_status(proposal_id), "updated")

    def test_metadata_stale_and_revoked_have_zero_acceptance_write(self):
        before = self._note("- legacy")
        stale_id = self._pending(before, "metadata", "metadata-stale", title="Stale title")
        self.notes.save_note(
            before.id, title="Human title", content_markdown=before.content_markdown,
            location=before.location, expected_revision=before.revision,
        )
        snapshot = self._note_row(before.id)
        self._assert_rejected_without_note_write(stale_id, before.id, "metadata-stale", snapshot)

        current = self.notes.get_note(before.id)
        revoked_id = self._pending(current, "metadata", "metadata-revoked", title="Revoked title")
        self._revoke_target_without_note_version_change(current)
        snapshot = self._note_row(before.id)
        self._assert_rejected_without_note_write(revoked_id, before.id, "metadata-revoked", snapshot)

    def test_redacted_common_target_still_allows_current_actor(self):
        before = self._note("FixtureSecret body\n")
        self.metadata.set_note_visibility(before.id, "redacted")
        self.metadata.set_note_targets(before.id, ("common",))
        before = self.notes.get_note(before.id)
        self.assertEqual(self.metadata.get_note_metadata(before.id).target_ids, ("common",))
        proposal_id = self._pending(before, "metadata", "common-redacted", title="Visible title")
        result = self._accept(proposal_id)
        self.assertEqual(result.note.title, "Visible title")
        self.assertEqual(result.note.content_markdown, before.content_markdown)
        self._assert_version_advanced(before, result.note)

    def test_metadata_payload_rejects_visibility_and_targets_even_from_pending_db(self):
        for forbidden_key, forbidden_value in (("ai_visibility", "full"), ("ai_targets", ["clawbot"])):
            with self.subTest(forbidden_key=forbidden_key):
                before = self._note("- legacy")
                request_id = f"forbidden-{forbidden_key}"
                proposal_id = self._pending(before, "metadata", request_id, title="Requested")
                with self.connections.write() as connection:
                    row = connection.execute(
                        "SELECT operation_payload FROM ai_receipt_inbox WHERE id = ?", (proposal_id,)
                    ).fetchone()
                    payload = json.loads(row["operation_payload"])
                    payload[forbidden_key] = forbidden_value
                    connection.execute(
                        "UPDATE ai_receipt_inbox SET operation_payload = ? WHERE id = ?",
                        (json.dumps(payload), proposal_id),
                    )
                snapshot = self._note_row(before.id)
                self._assert_rejected_without_note_write(proposal_id, before.id, request_id, snapshot)

    def test_deleted_target_is_rejected_without_acceptance_write(self):
        before = self._note()
        proposal_id = self._pending(before, "metadata", "deleted-target", title="Requested")
        with self.connections.write() as connection:
            connection.execute("UPDATE notes SET is_deleted = 1 WHERE id = ?", (before.id,))
        snapshot = self._note_row(before.id)
        self._assert_rejected_without_note_write(proposal_id, before.id, "deleted-target", snapshot)

    def test_prepend_canonicalizer_failure_is_terminal_and_zero_write(self):
        before = self._note()
        proposal_id = self._pending(before, "prepend", "prepend-failed", content="new")
        snapshot = self._note_row(before.id)
        with patch.object(MarkdownCanonicalizer, "canonicalize_markdown", side_effect=CanonicalizationError("fixture failure")):
            self._assert_rejected_without_note_write(proposal_id, before.id, "prepend-failed", snapshot)

    def test_accepted_and_rejected_receipts_are_idempotent(self):
        accepted_before = self._note()
        accepted_id = self._pending(accepted_before, "prepend", "accept-once", content="new")
        accepted = self._accept(accepted_id)
        accepted_snapshot = self._note_row(accepted_before.id)
        replay = self._accept(accepted_id)
        self.assertTrue(replay.already_processed)
        self.assertEqual(self._note_row(accepted_before.id), accepted_snapshot)
        self.assertEqual(len(self._history("accept-once")), 1)

        rejected_before = self._note()
        rejected_id = self._pending(rejected_before, "metadata", "reject-once", title="Disallowed")
        self._revoke_target_without_note_version_change(rejected_before)
        rejected_snapshot = self._note_row(rejected_before.id)
        self._assert_rejected_without_note_write(rejected_id, rejected_before.id, "reject-once", rejected_snapshot)
        with self.assertRaises(ValueError):
            self._accept(rejected_id)
        self.assertEqual(self._note_row(rejected_before.id), rejected_snapshot)
        self.assertEqual(self._history("reject-once"), [])

    def test_permission_revoked_during_canonicalization_is_rechecked(self):
        before = self._note()
        proposal = self._pending(before, "append", "late-revoke", content="new")
        original = MarkdownCanonicalizer.canonicalize_markdown
        revoked = []

        def revoke(milkdown, markdown):
            if not revoked:
                with self.connections.write() as connection:
                    connection.execute("UPDATE notes SET ai_visibility = 'hidden' WHERE id = ?", (before.id,))
                revoked.append(self._note_row(before.id))
            return original(milkdown, markdown)

        with patch.object(MarkdownCanonicalizer, "canonicalize_markdown", autospec=True, side_effect=revoke):
            with self.assertRaisesRegex(ValueError, "无权"):
                self._accept(proposal)
        self.assertEqual(self._note_row(before.id), revoked[0])
        self.assertEqual(self.notes.get_note(before.id).updated_at, before.updated_at)
        self.assertEqual(self._history("late-revoke"), [])
        self.assertEqual(self.receipts.mcp_get_operation("late-revoke")["status"], "rejected")

    def test_metadata_destination_permission_revoked_without_target_version_change(self):
        before = self._note()
        destination = self._note(location=NoteLocation("Destination"))
        proposal = self._pending(before, "metadata", "directory-revoke", directory_id=destination.directory_id)
        self._revoke_target_without_note_version_change(destination)
        self._assert_rejected_without_note_write(proposal, before.id, "directory-revoke", self._note_row(before.id))

    def test_terminal_operation_cannot_be_reapplied_by_pending_receipt(self):
        for operation in ("prepend", "metadata"):
            with self.subTest(operation=operation):
                before = self._note()
                request = "terminal-" + operation
                values = {"content": "new"} if operation == "prepend" else {"title": "New title"}
                proposal = self._pending(before, operation, request, **values)
                self._accept(proposal)
                snapshot = self._note_row(before.id)
                terminal = self.receipts.mcp_get_operation(request)
                with self.connections.write() as connection:
                    connection.execute("UPDATE ai_receipt_inbox SET status = 'pending' WHERE id = ?", (proposal,))
                with self.assertRaises(ValueError):
                    self._accept(proposal)
                self.assertEqual(self._note_row(before.id), snapshot)
                self.assertEqual(self.receipts.mcp_get_operation(request), terminal)
                self.assertEqual(len(self._history(request)), 1)

    def test_missing_request_identity_cannot_fall_through_to_create(self):
        before = self._note()
        proposal = self._pending(before, "metadata", "missing-request", title="New title")
        with self.connections.write() as connection:
            connection.execute("UPDATE ai_receipt_inbox SET request_id = NULL WHERE id = ?", (proposal,))
        snapshot = self.notes.list_active_notes()
        with self.assertRaises(ValueError):
            self._accept(proposal)
        self.assertEqual(self.notes.list_active_notes(), snapshot)
        self.assertEqual(self.receipts.get_receipt(proposal).status, "rejected")


if __name__ == "__main__":
    unittest.main()
