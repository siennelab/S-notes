"""F–G authorization-owner checks on disposable databases only."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from src.snotes.application.ai_access_metadata_service import AiAccessMetadataService
from src.snotes.application.ai_audit_service import AiAuditService
from src.snotes.application.ai_receipt_inbox_service import (
    AiReceiptAcceptFields,
    AiReceiptInboxService,
)
from src.snotes.application.ai_safe_read_service import AiSafeReadService, AiSafeReadSource
from src.snotes.application.ai_write_authorization import (
    AiWriteAuthorization,
    AiWriteExecutionContext,
)
from src.snotes.application.directory_service import DirectoryService
from src.snotes.application.local_mcp_write_service import (
    LocalMcpWriteService,
    note_version_token,
)
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
from tests.canonicalizer_test_double import CanonicalizerTestDouble


class AiWriteAuthorizationTests(unittest.TestCase):
    def setUp(self) -> None:
        temporary = tempfile.TemporaryDirectory(prefix="snotes-g-authorization-")
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.connections = initialize_database(self.root / "fixture.db")
        self.directory_repository = SQLiteDirectoriesRepository(self.connections)
        tag_repository = SQLiteTagsRepository(self.connections)
        note_repository = SQLiteNotesRepository(
            self.connections, self.directory_repository
        )
        self.notes = NoteService(note_repository)
        self.directories = DirectoryService(
            self.directory_repository, note_repository
        )
        self.tags = TagService(tag_repository)
        self.metadata = AiAccessMetadataService(note_repository)
        self.safe_reads = AiSafeReadService(self.root / "missing-redaction-rules.json")
        self.authorization = AiWriteAuthorization(
            self.directories, self.safe_reads
        )
        self.audit = AiAuditService(SQLiteAiAuditRepository(self.connections))
        self.receipts = AiReceiptInboxService(
            SQLiteAiReceiptInboxRepository(
                self.connections, self.directory_repository, tag_repository
            ),
            notes=self.notes,
            tags=self.tags,
            ai_metadata=self.metadata,
            mcp_operations=SQLiteAiMcpOperationRepository(
                self.connections, tag_repository
            ),
            canonicalizer=CanonicalizerTestDouble(lambda markdown: markdown),
            authorization=self.authorization,
        )
        self.writes = self._writes_for("clawbot")
        with self.connections.write() as connection:
            connection.execute(
                """INSERT OR REPLACE INTO app_settings
                (setting_key, setting_value, updated_at) VALUES (?, ?, ?)""",
                ("ai_modify_requires_confirmation", "0", "2026-09-28"),
            )

    def _writes_for(self, actor_target_id: str) -> LocalMcpWriteService:
        return LocalMcpWriteService(
            actor_target_id=actor_target_id,
            notes=self.notes,
            directories=self.directories,
            tags=self.tags,
            ai_metadata=self.metadata,
            safe_reads=self.safe_reads,
            audit=self.audit,
            receipt_inbox=self.receipts,
            authorization=self.authorization,
        )

    def _note(
        self,
        *,
        visibility: str = "full",
        targets: tuple[str, ...] = ("clawbot",),
    ):
        blank = self.notes.create_note(NoteLocation())
        note = self.notes.save_note(
            blank.id,
            title="Authorization fixture",
            content_markdown="Before\n",
            location=blank.location,
            expected_revision=blank.revision,
        )
        self.metadata.set_note_visibility(note.id, visibility)
        self.metadata.set_note_targets(note.id, targets)
        return self.notes.get_note(note.id)

    def _pending_append(self, note, *, actor_target_id: str, request_id: str) -> int:
        metadata = self.metadata.get_note_metadata(note.id)
        result = self.receipts.mcp_create_pending(
            actor_target_id=actor_target_id,
            actor_display=actor_target_id,
            request_id=request_id,
            operation_type="append",
            title=note.title,
            note_id=note.id,
            expected_updated_at=note.updated_at,
            payload={"content": "Human addition"},
            location=note.location,
            tags=(),
            body="Human addition",
            visibility=metadata.visibility,
            target_ids=metadata.target_ids,
        )
        return result["proposal_id"]

    def _accept(self, proposal_id: int):
        receipt = self.receipts.get_receipt(proposal_id)
        return self.receipts.accept_receipt(
            proposal_id,
            AiReceiptAcceptFields(
                title=receipt.title,
                body=receipt.body,
                main_category=receipt.main_category,
                sub_category=receipt.sub_category,
                tags=receipt.tags,
                visibility=receipt.ai_visibility,
                target_ids=("clawbot",),
            ),
        )

    def _empty_location(self, suffix: str) -> tuple[int, NoteLocation]:
        parent = self.directory_repository.create(f"G-{suffix}")
        child = self.directory_repository.create("空目录", parent.id)
        return child.id, NoteLocation(parent.name, child.name)

    def test_human_acceptance_and_direct_write_share_base_grants_but_not_redaction_runtime(self) -> None:
        source = AiSafeReadSource(
            note_id=1,
            title="Redacted",
            content="FICTIONAL SECRET",
            visibility="redacted",
            targets=("clawbot",),
        )
        self.assertTrue(self.authorization.can_write_note(
            "clawbot", source,
            context=AiWriteExecutionContext.HUMAN_ACCEPTANCE,
        ))
        self.assertFalse(self.authorization.can_write_note(
            "clawbot", source,
            context=AiWriteExecutionContext.MCP_DIRECT_WRITE,
        ))
        self.assertFalse(self.authorization.can_write_note(
            "gale", source,
            context=AiWriteExecutionContext.HUMAN_ACCEPTANCE,
        ))
        self.assertFalse(self.authorization.can_write_note(
            "clawbot", source,
            context=AiWriteExecutionContext.HUMAN_ACCEPTANCE,
            exact_edit=True,
        ))

    def test_same_owner_drives_human_redacted_accept_and_direct_fail_closed(self) -> None:
        note = self._note(visibility="redacted")
        proposal_id = self._pending_append(
            note, actor_target_id="clawbot", request_id="human-redacted"
        )
        with patch.object(
            self.authorization,
            "can_write_note",
            wraps=self.authorization.can_write_note,
        ) as authorize:
            accepted = self._accept(proposal_id)
            self.assertTrue(accepted.outcome.accepted)
            self.assertIn("Human addition", accepted.note.content_markdown)
            direct = self.writes.append_note(
                request_id="direct-redacted",
                note_id=note.id,
                content="Direct addition",
                expected_version=note_version_token(
                    accepted.note.id, accepted.note.updated_at
                ),
            )

        self.assertEqual(direct["status"], "not_found")
        contexts = [call.kwargs["context"] for call in authorize.call_args_list]
        self.assertIn(AiWriteExecutionContext.HUMAN_ACCEPTANCE, contexts)
        self.assertIn(AiWriteExecutionContext.MCP_DIRECT_WRITE, contexts)
        self.assertNotIn(
            "Direct addition", self.notes.get_note(note.id).content_markdown
        )

    def test_unauthorized_actor_is_denied_by_human_and_direct_paths(self) -> None:
        note = self._note(targets=("clawbot",))
        proposal_id = self._pending_append(
            note, actor_target_id="gale", request_id="human-unauthorized"
        )
        human = self._accept(proposal_id)
        self.assertFalse(human.outcome.accepted)
        self.assertIsNone(human.note)

        direct = self._writes_for("gale").append_note(
            request_id="direct-unauthorized",
            note_id=note.id,
            content="Denied",
            expected_version=note_version_token(note.id, note.updated_at),
        )
        self.assertEqual(direct["status"], "not_found")
        self.assertEqual(self.notes.get_note(note.id), note)

    def test_authorized_full_direct_write_succeeds_through_owner(self) -> None:
        note = self._note()
        result = self.writes.append_note(
            request_id="direct-authorized",
            note_id=note.id,
            content="Allowed",
            expected_version=note_version_token(note.id, note.updated_at),
        )
        self.assertEqual(result["status"], "updated")
        self.assertIn("Allowed", self.notes.get_note(note.id).content_markdown)

    def test_same_empty_directory_accepts_name_then_id_selector(self) -> None:
        directory_id, location = self._empty_location("selector-parity")
        by_name = self.writes.create_note(
            "empty-by-name", "By name", "body",
            main_category=location.main, sub_category=location.sub,
        )
        self.assertEqual(by_name["status"], "created")
        self.notes.soft_delete_note(by_name["note_id"])
        self.assertFalse(any(
            note.directory_id == directory_id
            for note in self.notes.list_active_notes()
        ))

        by_id = self.writes.create_note(
            "empty-by-id", "By id", "body", directory_id=directory_id,
        )
        self.assertEqual(by_id["status"], "created")
        self.assertEqual(self.notes.get_note(by_id["note_id"]).location, location)

    def test_missing_and_deleted_directories_are_rejected_by_both_selectors(self) -> None:
        directory_id, location = self._empty_location("invalid")
        self.directory_repository.soft_delete_tree(directory_id)
        cases = (
            {"directory_id": directory_id},
            {"main_category": location.main, "sub_category": location.sub},
            {"directory_id": 999_999},
            {"main_category": "G-missing", "sub_category": "空目录"},
        )
        for index, selector in enumerate(cases):
            with self.subTest(selector=selector):
                result = self.writes.create_note(
                    f"invalid-{index}", "Rejected", "body", **selector
                )
                self.assertEqual(result["status"], "rejected")

    def test_nonempty_directory_does_not_require_a_visible_note_witness(self) -> None:
        directory_id, location = self._empty_location("nonempty")
        hidden = self.notes.create_note(location)
        self.assertEqual(hidden.directory_id, directory_id)
        self.assertEqual(hidden.ai_visibility, "hidden")

        result = self.writes.create_note(
            "nonempty-by-id", "Allowed", "body", directory_id=directory_id,
        )
        self.assertEqual(result["status"], "created")
        self.assertEqual(self.notes.get_note(result["note_id"]).location, location)

    def test_directory_resolution_returns_active_tree_snapshot(self) -> None:
        directory_id, location = self._empty_location("snapshot")
        by_id = self.authorization.resolve_directory(directory_id=directory_id)
        by_name = self.authorization.resolve_directory(location=location)
        self.assertEqual(by_id, by_name)
        self.assertEqual(by_id.id, directory_id)
        self.assertEqual(len(by_id.snapshot), 2)
        self.assertEqual(by_id.snapshot[-1].id, directory_id)
        self.assertEqual(
            tuple(item.name for item in by_id.snapshot), location.values()
        )


if __name__ == "__main__":
    unittest.main()
