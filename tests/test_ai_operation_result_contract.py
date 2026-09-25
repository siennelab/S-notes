"""A checkpoint: operation result compatibility on disposable HOME and storage."""
from __future__ import annotations

import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from src.snotes.application.ai_access_metadata_service import AiAccessMetadataService
from src.snotes.application.ai_audit_service import AiAuditService
from src.snotes.application.ai_receipt_inbox_service import AiReceiptAcceptFields, AiReceiptInboxService
from src.snotes.application.ai_safe_read_service import AiSafeReadService
from src.snotes.application.directory_service import DirectoryService
from src.snotes.application.local_mcp_write_service import LocalMcpWriteService
from src.snotes.application.note_service import NoteService
from src.snotes.application.tag_service import TagService
from src.snotes.storage.repositories.ai_audit import SQLiteAiAuditRepository
from src.snotes.storage.repositories.ai_mcp_operations import SQLiteAiMcpOperationRepository
from src.snotes.storage.repositories.ai_receipts import SQLiteAiReceiptInboxRepository
from src.snotes.storage.repositories.directories import SQLiteDirectoriesRepository
from src.snotes.storage.repositories.notes import SQLiteNotesRepository
from src.snotes.storage.repositories.reminders import SQLiteReminderRepository
from src.snotes.storage.repositories.tags import SQLiteTagsRepository
from src.snotes.storage.schema import initialize_database
from src.snotes.transport.mcp.tools import LocalMcpToolAdapter


class OperationResultCompatibilityTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="snotes-operation-contract-")
        self.addCleanup(temporary.cleanup)
        root = Path(temporary.name)
        environment = patch.dict(os.environ, {"HOME": str(root), "S_NOTES_DATA_ROOT": str(root)})
        environment.start()
        self.addCleanup(environment.stop)
        self.connections = initialize_database(root / "fixture.db")
        directories = SQLiteDirectoriesRepository(self.connections)
        tags = SQLiteTagsRepository(self.connections)
        notes = SQLiteNotesRepository(self.connections, directories)
        self.notes = NoteService(notes)
        metadata = AiAccessMetadataService(notes)
        self.operations = SQLiteAiMcpOperationRepository(self.connections, tags)
        self.receipts = AiReceiptInboxService(
            SQLiteAiReceiptInboxRepository(self.connections, directories, tags),
            notes=self.notes, tags=TagService(tags), ai_metadata=metadata,
            mcp_operations=self.operations,
        )
        self.writes = LocalMcpWriteService(
            actor_target_id="clawbot", notes=self.notes,
            directories=DirectoryService(directories), tags=TagService(tags),
            ai_metadata=metadata, safe_reads=AiSafeReadService(root / "unused-rules.json"),
            audit=AiAuditService(SQLiteAiAuditRepository(self.connections)),
            receipt_inbox=self.receipts, create_confirmation_required=True,
        )
        self.adapter = LocalMcpToolAdapter(self.writes)

    def _store_fixture(self, result, operation_type="create", actor="clawbot"):
        with self.connections.write() as connection:
            connection.execute(
                """INSERT INTO ai_operations
                (request_id, actor_target_id, operation_type, status, result_json, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?)""",
                (result["request_id"], actor, operation_type, result["status"],
                 json.dumps(result), "2026-09-25T01:00:00", "2026-09-25T02:00:00"),
            )

    def test_manual_create_acceptance_reports_formal_change_and_survives_reread(self):
        pending = self.writes.create_note("accepted-create", "Fictional", "Body\n")
        self.assertEqual(pending["status"], "pending_confirmation")
        before = self.adapter.get_operation_status("accepted-create")
        self.assertFalse(before.changed_formal_note)
        self.assertTrue(before.pending_confirmation)
        receipt = self.receipts.get_receipt(pending["proposal_id"])
        fields = AiReceiptAcceptFields(
            title=receipt.title, body=receipt.body,
            main_category=receipt.main_category, sub_category=receipt.sub_category,
            tags=receipt.tags, visibility=receipt.ai_visibility, target_ids=("clawbot",),
        )
        # Canonicalization is outside A; adjacent regression tests exercise real Milkdown.
        with patch("src.snotes.application.ai_receipt_inbox_service.canonicalize_new_note", return_value="Body\n"):
            accepted = self.receipts.accept_receipt(receipt.id, fields)
        after = self.adapter.get_operation_status("accepted-create")
        self.assertEqual(after.status, "created")
        self.assertTrue(after.changed_formal_note)
        self.assertFalse(after.pending_confirmation)
        self.assertEqual(after.processing_mode, "human_approved")
        self.assertEqual((after.note_id, after.proposal_id), (accepted.note.id, receipt.id))
        self.assertEqual(self.notes.get_note(accepted.note.id).content_markdown, "Body\n")
        with self.connections.read() as connection:
            row = connection.execute(
                "SELECT result_json FROM ai_operations WHERE request_id = ?", ("accepted-create",)
            ).fetchone()
        payload = json.loads(row["result_json"])
        self.assertIs(payload["modified_formal_note"], True)
        self.assertIs(payload["entered_confirmation"], False)
        self.assertNotIn("changed", payload)
        self.assertNotIn("pending_confirmation", payload)
        replay = self.receipts.accept_receipt(receipt.id, fields)
        self.assertTrue(replay.already_processed)
        self.assertEqual(len(self.notes.list_active_notes()), 1)
        reopened = SQLiteAiMcpOperationRepository(
            initialize_database(self.connections.database_path), SQLiteTagsRepository(self.connections)
        )
        self.assertTrue(reopened.get_operation("accepted-create", "clawbot")["modified_formal_note"])

    def test_legacy_notes_aliases_and_current_keys_remain_readable(self):
        cases = (
            ("legacy-created", "created", "human_approved", {"changed": True, "pending_confirmation": False}, True, False),
            ("legacy-rejected", "rejected", "human_rejected", {"changed": False, "pending_confirmation": False}, False, False),
            ("legacy-pending", "pending_confirmation", "pending_confirmation", {"changed": False, "pending_confirmation": True}, False, True),
            ("existing-updated", "updated", "human_approved", {"modified_formal_note": True, "entered_confirmation": False}, True, False),
        )
        for request_id, status, mode, flags, changed, pending in cases:
            with self.subTest(request_id=request_id):
                result = dict(status=status, request_id=request_id, note_id=42, proposal_id=7,
                              title="旧结果", handling_mode=mode, reason="", **flags)
                self._store_fixture(result)
                response = self.adapter.get_operation_status(request_id)
                self.assertEqual(response.status, status)
                self.assertEqual(response.changed_formal_note, changed)
                self.assertEqual(response.pending_confirmation, pending)
                self.assertEqual((response.note_id, response.proposal_id), (42, 7))
                self.assertEqual(response.processing_mode, mode)
                self.assertEqual(response.created_at, "2026-09-25T01:00:00")
                self.assertEqual(response.updated_at, "2026-09-25T02:00:00")
                with self.connections.read() as connection:
                    stored = connection.execute("SELECT result_json FROM ai_operations WHERE request_id = ?", (request_id,)).fetchone()
                self.assertEqual(json.loads(stored["result_json"]), result)

    def test_reminder_existing_and_current_writer_results_preserve_all_fields(self):
        reminders = SQLiteReminderRepository(self.connections)
        for request_id, existing in (("old-reminder", True), ("new-reminder", False)):
            with self.subTest(existing=existing):
                result = dict(status="updated", request_id=request_id, table_id=8, item_id=9,
                              reminder_id=9, title="Fictional reminder", handling_mode="direct",
                              modified_formal_reminder=True, entered_confirmation=False,
                              reason="", request_fingerprint="fixture-fingerprint")
                if existing:
                    self._store_fixture(result, operation_type="complete_reminder_item")
                else:
                    reminders.store_operation(actor_target_id="clawbot", operation_type="complete_reminder_item", result=result)
                reminder_read = reminders.get_operation(request_id, "clawbot")
                notes_read = self.operations.get_operation(request_id, "clawbot")
                self.assertEqual(notes_read, reminder_read)
                for key, value in result.items():
                    self.assertEqual(notes_read[key], value)
                response = self.adapter.get_operation_status(request_id)
                self.assertEqual(response.status, "updated")
                self.assertEqual(response.operation_type, "complete_reminder_item")
                self.assertFalse(response.changed_formal_note)
                self.assertFalse(response.pending_confirmation)
                self.assertIsNone(response.note_id)
                self.assertIsNone(self.operations.get_operation(request_id, "gale"))


class NotesResultSerializationTests(unittest.TestCase):
    def test_canonical_notes_roundtrip_has_one_flag_vocabulary(self):
        from src.snotes.application.notes_operation_result import (
            NotesOperationResult, deserialize_operation_result_json,
            serialize_notes_operation_result,
        )
        result = NotesOperationResult(
            status="created", request_id="canonical-create", note_id=42,
            title="人工接受", handling_mode="human_approved",
            modified_formal_note=True, entered_confirmation=False,
            reason="", proposal_id=7,
        )
        encoded = serialize_notes_operation_result(result)
        payload = json.loads(encoded)
        self.assertEqual(payload, result.to_dict())
        self.assertNotIn("changed", payload)
        self.assertNotIn("pending_confirmation", payload)
        self.assertIs(payload["modified_formal_note"], True)
        self.assertIs(payload["entered_confirmation"], False)
        self.assertEqual(deserialize_operation_result_json(encoded), result.to_dict())
        self.assertEqual(serialize_notes_operation_result(result), encoded)

    def test_canonical_false_flags_take_precedence_over_legacy_aliases(self):
        from src.snotes.application.notes_operation_result import deserialize_operation_result_json
        stored = dict(status="rejected", request_id="mixed-legacy", note_id=None,
                      modified_formal_note=False, entered_confirmation=False,
                      changed=True, pending_confirmation=True)
        result = deserialize_operation_result_json(json.dumps(stored))
        self.assertIs(result["modified_formal_note"], False)
        self.assertIs(result["entered_confirmation"], False)
