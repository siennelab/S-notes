"""B–E concurrency and execution-context regressions on disposable storage."""
from __future__ import annotations

import json
import os
from pathlib import Path
import sqlite3
import subprocess
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from src.snotes.application.ai_access_metadata_service import AiAccessMetadataService
from src.snotes.application.ai_acceptance_outcome import AcceptanceKind
from src.snotes.application.ai_audit_service import AiAuditService
from src.snotes.application.ai_receipt_inbox_service import AiReceiptAcceptFields, AiReceiptInboxService
from src.snotes.application.ai_safe_read_service import AiSafeReadService
from src.snotes.application.ai_write_authorization import AiWriteAuthorization
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


class AcceptanceBoundaryTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="snotes-be-boundary-")
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        environment = patch.dict(os.environ, {"HOME": str(self.root), "S_NOTES_DATA_ROOT": str(self.root)})
        environment.start()
        self.addCleanup(environment.stop)
        self.connections = initialize_database(self.root / "fixture.db")
        directories = SQLiteDirectoriesRepository(self.connections)
        tags = SQLiteTagsRepository(self.connections)
        notes = SQLiteNotesRepository(self.connections, directories)
        self.notes = NoteService(notes)
        self.metadata = AiAccessMetadataService(notes)
        self.operations = SQLiteAiMcpOperationRepository(self.connections, tags)
        self.directories = DirectoryService(directories)
        self.canonical_hook = lambda markdown: markdown
        self.receipts = AiReceiptInboxService(
            SQLiteAiReceiptInboxRepository(self.connections, directories, tags),
            notes=self.notes, tags=TagService(tags), ai_metadata=self.metadata,
            mcp_operations=self.operations,
            canonicalizer=SimpleNamespace(canonicalize_markdown=lambda markdown: self.canonical_hook(markdown)),
            authorization=AiWriteAuthorization(directories=self.directories),
        )
        self.rules = self.root / "rules.json"
        self.rules.write_text(json.dumps({"secret": "[redacted]"}), encoding="utf-8")
        self.writes = LocalMcpWriteService(
            actor_target_id="yan", notes=self.notes,
            directories=self.directories, tags=TagService(tags),
            ai_metadata=self.metadata, safe_reads=AiSafeReadService(self.rules),
            audit=AiAuditService(SQLiteAiAuditRepository(self.connections)),
            receipt_inbox=self.receipts,
        )

    def note(self, *, visibility="full", location=None):
        blank = self.notes.create_note(location or NoteLocation())
        saved = self.notes.save_note(blank.id, title="Fixture", content_markdown="Alpha\n\nOmega\n",
                                     location=blank.location, expected_revision=blank.revision)
        self.metadata.set_note_visibility(saved.id, visibility)
        self.metadata.set_note_targets(saved.id, ("yan",))
        return self.notes.get_note(saved.id)

    def pending(self, note, operation="edit", request_id="proposal", **values):
        arguments = dict(request_id=request_id, note_id=note.id,
                         expected_version=note_version_token(note.id, note.updated_at))
        if operation == "edit":
            arguments["edits"] = [{"old_markdown": "Alpha", "new_markdown": "Beta"}]
        elif operation in {"append", "prepend"}:
            arguments["content"] = "Addition\n"
        arguments.update(values)
        method = "update_note_metadata" if operation == "metadata" else f"{operation}_note"
        result = getattr(self.writes, method)(**arguments)
        self.assertEqual(result["status"], "pending_confirmation", result)
        return result["proposal_id"]

    def accept(self, proposal_id):
        receipt = self.receipts.get_receipt(proposal_id)
        return self.receipts.accept_receipt(proposal_id, AiReceiptAcceptFields(
            title=receipt.title, body=receipt.body, main_category=receipt.main_category,
            sub_category=receipt.sub_category, tags=receipt.tags,
            visibility=receipt.ai_visibility, target_ids=("yan",),
        ))

    def history_count(self, request_id="proposal"):
        with self.connections.read() as connection:
            return connection.execute("SELECT COUNT(*) FROM ai_note_history WHERE request_id = ?", (request_id,)).fetchone()[0]

    def test_application_and_storage_work_without_loading_ui_canonicalizer(self):
        result = subprocess.run(
            [sys.executable, "-B", "-c", r"""
import importlib.abc
import sys
class NoGui(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        if fullname.startswith(('PySide6', 'src.snotes.ui')):
            raise AssertionError('Unexpected GUI dependency: ' + fullname)
sys.meta_path.insert(0, NoGui())
from src.snotes.application.ai_receipt_inbox_service import AiReceiptInboxService
from src.snotes.application.local_mcp_write_service import LocalMcpWriteService
from src.snotes.storage.repositories.ai_mcp_operations import SQLiteAiMcpOperationRepository
from src.snotes.application.note_canonical_candidate import build_canonical_edit_candidate
candidate = build_canonical_edit_candidate('Alpha\nOmega', [{'old_markdown': 'Alpha', 'new_markdown': 'Beta'}], canonicalize=lambda value: value)
assert candidate.final_markdown == 'Beta\nOmega'
"""],
            cwd=Path(__file__).resolve().parents[1], env=os.environ.copy(),
            capture_output=True, text=True, timeout=30,
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_edit_canonicalization_can_acquire_an_independent_sqlite_write_lock(self):
        note = self.note()
        proposal = self.pending(note)
        calls = []

        def canonicalize(markdown):
            # An immediate second writer proves the acceptance path holds no
            # SQLite write lock while the injected adapter executes.
            connection = sqlite3.connect(self.connections.database_path, timeout=0)
            try:
                connection.execute("BEGIN IMMEDIATE")
                connection.rollback()
            finally:
                connection.close()
            calls.append(markdown)
            return markdown

        self.canonical_hook = canonicalize
        result = self.accept(proposal)
        self.assertTrue(calls)
        self.assertEqual(result.note.content_markdown, "Beta\n\nOmega\n")
        self.assertEqual(self.history_count(), 1)

    def test_edit_note_change_during_canonicalization_conflicts_without_overwrite(self):
        note = self.note()
        proposal = self.pending(note)
        changed = []

        def canonicalize(markdown):
            if not changed:
                changed.append(self.notes.save_note(
                    note.id, title=note.title, content_markdown="Concurrent human body\n",
                    location=note.location, expected_revision=note.revision,
                ))
            return markdown

        self.canonical_hook = canonicalize
        result = self.accept(proposal)
        self.assertEqual(result.outcome.kind, AcceptanceKind.CONFLICT)
        self.assertEqual(self.notes.get_note(note.id), changed[0])
        self.assertEqual(self.history_count(), 0)
        self.assertEqual(self.receipts.mcp_get_operation("proposal")["status"], "conflict")

    def test_edit_payload_change_during_canonicalization_conflicts_without_write(self):
        note = self.note()
        proposal = self.pending(note)
        changed = []

        def canonicalize(markdown):
            if not changed:
                with self.connections.write() as connection:
                    connection.execute(
                        "UPDATE ai_receipt_inbox SET operation_payload = ? WHERE id = ?",
                        (json.dumps({"edits": [{"old_markdown": "Alpha", "new_markdown": "Other"}]}), proposal),
                    )
                changed.append(True)
            return markdown

        self.canonical_hook = canonicalize
        result = self.accept(proposal)
        self.assertEqual(result.outcome.kind, AcceptanceKind.CONFLICT)
        self.assertEqual(self.notes.get_note(note.id), note)
        self.assertEqual(self.history_count(), 0)
        self.assertEqual(self.receipts.mcp_get_operation("proposal")["status"], "conflict")

    def test_final_commit_rechecks_receipt_parse_status(self):
        note = self.note()
        proposal = self.pending(note)
        original = self.operations.accept_pending_modification

        def change_receipt_then_commit(**values):
            with self.connections.write() as connection:
                connection.execute("UPDATE ai_receipt_inbox SET parse_status = 'failed' WHERE id = ?", (proposal,))
            return original(**values)

        with patch.object(self.operations, "accept_pending_modification", side_effect=change_receipt_then_commit):
            result = self.accept(proposal)
        self.assertEqual(result.outcome.kind, AcceptanceKind.CONFLICT)
        self.assertEqual(self.notes.get_note(note.id), note)
        self.assertEqual(self.history_count(), 0)
        self.assertEqual(self.receipts.mcp_get_operation("proposal")["status"], "conflict")

    def test_final_commit_rechecks_grant_fingerprint_without_version_change(self):
        note = self.note()
        proposal = self.pending(note)
        original = self.operations.accept_pending_modification

        def revoke_then_commit(**values):
            with self.connections.write() as connection:
                connection.execute("DELETE FROM note_ai_targets WHERE note_id = ?", (note.id,))
            return original(**values)

        with patch.object(self.operations, "accept_pending_modification", side_effect=revoke_then_commit):
            result = self.accept(proposal)
        self.assertEqual(result.outcome.kind, AcceptanceKind.CONFLICT)
        self.assertEqual(self.notes.get_note(note.id), note)
        self.assertEqual(self.history_count(), 0)
        self.assertEqual(self.receipts.mcp_get_operation("proposal")["status"], "conflict")

    def test_moving_directory_witness_does_not_block_final_commit(self):
        note = self.note()
        destination = self.note(location=NoteLocation("Destination"))
        proposal = self.pending(note, "metadata", directory_id=destination.directory_id)
        original = self.operations.accept_pending_modification

        def move_authorization_witness_then_commit(**values):
            with self.connections.write() as connection:
                connection.execute("UPDATE notes SET directory_id = ? WHERE id = ?", (note.directory_id, destination.id))
            return original(**values)

        with patch.object(self.operations, "accept_pending_modification", side_effect=move_authorization_witness_then_commit):
            result = self.accept(proposal)
        self.assertEqual(result.outcome.kind, AcceptanceKind.SUCCESS)
        self.assertEqual(self.notes.get_note(note.id).directory_id, destination.directory_id)
        self.assertEqual(self.history_count(), 1)

    def test_final_commit_rechecks_destination_directory_snapshot(self):
        note = self.note()
        destination = self.note(location=NoteLocation("Destination"))
        proposal = self.pending(note, "metadata", directory_id=destination.directory_id)
        original = self.operations.accept_pending_modification

        def rename_destination_then_commit(**values):
            self.directories.rename_directory(destination.directory_id, "Renamed Destination")
            return original(**values)

        with patch.object(
            self.operations,
            "accept_pending_modification",
            side_effect=rename_destination_then_commit,
        ):
            result = self.accept(proposal)
        self.assertEqual(result.outcome.kind, AcceptanceKind.CONFLICT)
        self.assertEqual(self.notes.get_note(note.id), note)
        self.assertEqual(self.history_count(), 0)
        self.assertEqual(self.receipts.mcp_get_operation("proposal")["status"], "conflict")

    def test_final_commit_rechecks_deleted_destination_directory(self):
        note = self.note()
        destination = self.directories.create_directory("Empty Destination")
        proposal = self.pending(note, "metadata", directory_id=destination.id)
        original = self.operations.accept_pending_modification

        def delete_destination_then_commit(**values):
            self.directories.soft_delete_directory(destination.id)
            return original(**values)

        with patch.object(self.operations, "accept_pending_modification", side_effect=delete_destination_then_commit):
            result = self.accept(proposal)
        self.assertEqual(result.outcome.kind, AcceptanceKind.CONFLICT)
        self.assertEqual(self.notes.get_note(note.id), note)
        self.assertEqual(self.history_count(), 0)

    def test_commit_failure_rolls_back_note_history_receipt_and_operation(self):
        note = self.note()
        proposal = self.pending(note)
        receipt_before = self.receipts.get_receipt(proposal)
        operation_before = self.receipts.mcp_get_operation("proposal")
        original = self.operations._write_content_in_connection

        def write_then_fail(*args, **values):
            original(*args, **values)
            raise RuntimeError("synthetic commit failure")

        with patch.object(self.operations, "_write_content_in_connection", side_effect=write_then_fail):
            with self.assertRaisesRegex(RuntimeError, "synthetic commit failure"):
                self.accept(proposal)
        self.assertEqual(self.notes.get_note(note.id), note)
        self.assertEqual(self.history_count(), 0)
        self.assertEqual(self.receipts.get_receipt(proposal), receipt_before)
        self.assertEqual(self.receipts.mcp_get_operation("proposal"), operation_before)

    def test_direct_edit_rechecks_permission_after_canonicalization(self):
        note = self.note()
        with self.connections.write() as connection:
            connection.execute(
                "INSERT OR REPLACE INTO app_settings(setting_key, setting_value, updated_at) VALUES (?, ?, ?)",
                ("ai_modify_requires_confirmation", "0", "2026-09-28"),
            )
        revoked = []

        def canonicalize(markdown):
            if not revoked:
                with self.connections.write() as connection:
                    connection.execute("DELETE FROM note_ai_targets WHERE note_id = ?", (note.id,))
                revoked.append(True)
            return markdown

        self.canonical_hook = canonicalize
        result = self.writes.edit_note(
            request_id="revoked-direct-edit", note_id=note.id,
            expected_version=note_version_token(note.id, note.updated_at),
            edits=[{"old_markdown": "Alpha", "new_markdown": "Beta"}],
        )
        self.assertEqual(result["status"], "not_found")
        self.assertEqual(self.notes.get_note(note.id), note)
        self.assertEqual(self.history_count("revoked-direct-edit"), 0)

    def test_human_acceptance_keeps_redacted_note_and_directory_authorization_without_rules(self):
        note = self.note(visibility="redacted")
        destination = self.note(visibility="redacted", location=NoteLocation("Destination"))
        proposal = self.pending(note, "metadata", title="Human accepted", directory_id=destination.directory_id)
        self.rules.unlink()
        accepted = self.accept(proposal)
        self.assertEqual(accepted.note.title, "Human accepted")
        self.assertEqual(accepted.note.directory_id, destination.directory_id)
        self.assertEqual(self.history_count(), 1)

    def test_human_body_acceptance_without_rules_and_mcp_direct_fail_closed(self):
        note = self.note(visibility="redacted")
        proposal = self.pending(note, "append")
        self.rules.unlink()
        accepted = self.accept(proposal).note
        with self.connections.write() as connection:
            connection.execute(
                "INSERT OR REPLACE INTO app_settings(setting_key, setting_value, updated_at) VALUES (?, ?, ?)",
                ("ai_modify_requires_confirmation", "0", "2026-09-28"),
            )
        direct = self.writes.append_note(
            request_id="direct-denied", note_id=note.id, content="Never written",
            expected_version=note_version_token(accepted.id, accepted.updated_at),
        )
        self.assertEqual(direct["status"], "not_found")
        self.assertEqual(self.notes.get_note(note.id), accepted)
        self.assertEqual(self.history_count("direct-denied"), 0)


if __name__ == "__main__":
    unittest.main()
