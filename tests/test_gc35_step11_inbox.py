"""Step 11 GreenCloud inbox, acceptance, and durable terminal receipts."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from src.snotes.application.ai_access_metadata_service import AiAccessMetadataService
from src.snotes.application.ai_acceptance_outcome import AcceptanceKind
from src.snotes.application.ai_receipt_inbox_service import (
    AiReceiptAcceptFields,
    AiReceiptInboxService,
)
from src.snotes.application.ai_write_authorization import AiWriteAuthorization
from src.snotes.application.directory_service import DirectoryService
from src.snotes.application.local_mcp_write_service import note_version_token
from src.snotes.application.note_service import NoteService
from src.snotes.application.tag_service import TagService
from src.snotes.domain.ai_receipts import GaleReceiptImport
from src.snotes.domain.greencloud import GREEN_CLOUD_SOURCE, GreenCloudSubmission
from src.snotes.domain.models import NoteLocation
from src.snotes.storage.repositories.ai_mcp_operations import SQLiteAiMcpOperationRepository
from src.snotes.storage.repositories.ai_receipts import SQLiteAiReceiptInboxRepository
from src.snotes.storage.repositories.directories import SQLiteDirectoriesRepository
from src.snotes.storage.repositories.notes import SQLiteNotesRepository
from src.snotes.storage.repositories.tags import SQLiteTagsRepository
from src.snotes.storage.schema import initialize_database
from src.snotes.ui.markdown_canonicalizer import CanonicalizationError
from tests.canonicalizer_test_double import CanonicalizerTestDouble


VERSION = "snotes-v1-" + "a" * 64


class Step11InboxTests(unittest.TestCase):
    def setUp(self) -> None:
        temporary = tempfile.TemporaryDirectory(prefix="snotes-step11-inbox-")
        self.addCleanup(temporary.cleanup)
        self.connections = initialize_database(Path(temporary.name) / "fixture.db")
        directory_repository = SQLiteDirectoriesRepository(self.connections)
        tags_repository = SQLiteTagsRepository(self.connections)
        notes_repository = SQLiteNotesRepository(self.connections, directory_repository)
        self.notes = NoteService(notes_repository)
        self.tags = TagService(tags_repository)
        self.metadata = AiAccessMetadataService(notes_repository)
        self.canonicalizer = CanonicalizerTestDouble(
            lambda value: value.rstrip() + "\n" if value.strip() else ""
        )
        repository = SQLiteAiReceiptInboxRepository(
            self.connections,
            directory_repository,
            tags_repository,
        )
        self.service = AiReceiptInboxService(
            repository,
            notes=self.notes,
            tags=self.tags,
            ai_metadata=self.metadata,
            mcp_operations=SQLiteAiMcpOperationRepository(
                self.connections,
                tags_repository,
            ),
            canonicalizer=self.canonicalizer,
            authorization=AiWriteAuthorization(
                directories=DirectoryService(directory_repository)
            ),
        )

    def _submission(
        self,
        operation: str,
        request_id: str,
        *,
        actor: str = "claude-code",
        uuid: str | None = None,
        note_id: int | None = None,
        expected_version: str | None = None,
        payload: dict | None = None,
    ) -> GreenCloudSubmission:
        if payload is None:
            payload = {
                "title": "Fictional Cloud note",
                "content": "# Fictional\n",
                "directory_id": None,
                "main_category": None,
                "sub_category": None,
                "tags": ["fictional"],
            }
        return GreenCloudSubmission(
            submission_uuid=uuid or f"uuid-{actor}-{request_id}",
            source_actor=actor,
            request_id=request_id,
            operation_type=operation,
            note_id=note_id,
            expected_version=expected_version,
            payload=payload,
            created_at="2026-10-04T00:00:00Z",
            delivery_state="delivered",
            delivered_at="2026-10-04T00:00:01Z",
        )

    def _note(
        self,
        *,
        body: str = "base unique\n",
        visibility: str = "full",
        targets: tuple[str, ...] = ("common",),
    ):
        blank = self.notes.create_note(NoteLocation())
        saved = self.notes.save_note(
            blank.id,
            title="Fictional target",
            content_markdown=body,
            location=blank.location,
            expected_revision=blank.revision,
        )
        self.metadata.set_note_visibility(saved.id, visibility)
        self.metadata.set_note_targets(saved.id, targets)
        return self.notes.get_note(saved.id)

    def _accept(self, receipt_id: int):
        receipt = self.service.get_receipt(receipt_id)
        assert receipt is not None
        return self.service.accept_receipt(
            receipt_id,
            AiReceiptAcceptFields(
                title=receipt.title,
                body=receipt.body,
                main_category=receipt.main_category,
                sub_category=receipt.sub_category,
                tags=receipt.tags,
                visibility=receipt.ai_visibility,
                target_ids=(receipt.actor_target_id,) if receipt.actor_target_id else (),
            ),
        )

    def _note_count(self) -> int:
        with self.connections.read() as connection:
            return int(connection.execute("SELECT COUNT(*) FROM notes").fetchone()[0])

    def test_import_is_source_scoped_actor_safe_and_never_creates_formal_note(self) -> None:
        before = self._note_count()
        claude, created = self.service.import_greencloud_pending(
            self._submission("create", "same-request", actor="claude-code")
        )
        clawbot, other_created = self.service.import_greencloud_pending(
            self._submission("create", "same-request", actor="clawbot")
        )
        replay, replay_created = self.service.import_greencloud_pending(
            self._submission("create", "same-request", actor="claude-code")
        )
        self.assertTrue(created)
        self.assertTrue(other_created)
        self.assertFalse(replay_created)
        self.assertEqual(replay.id, claude.id)
        self.assertNotEqual(claude.id, clawbot.id)
        self.assertEqual({claude.source, clawbot.source}, {GREEN_CLOUD_SOURCE})
        self.assertNotEqual(claude.request_id, clawbot.request_id)
        self.assertNotEqual(claude.idempotency_key, clawbot.idempotency_key)
        self.assertEqual(self._note_count(), before)
        with self.assertRaisesRegex(ValueError, "幂等身份冲突"):
            self.service.import_greencloud_pending(
                self._submission(
                    "create",
                    "same-request",
                    actor="claude-code",
                    uuid="different-cloud-submission",
                )
            )

    def test_import_retry_after_local_decision_or_note_change_keeps_original_receipt(self) -> None:
        create = self._submission("create", "ack-retry-create")
        receipt, _ = self.service.import_greencloud_pending(create)
        self._accept(receipt.id)
        replay, created = self.service.import_greencloud_pending(create)
        self.assertFalse(created)
        self.assertEqual(replay.id, receipt.id)
        self.assertEqual(replay.status, "accepted")
        note = self._note()
        proposal = self._submission("append", "ack-retry-note-change", note_id=note.id,
            expected_version=note_version_token(note.id, note.updated_at), payload={"content": "tail"})
        pending, _ = self.service.import_greencloud_pending(proposal)
        self.notes.save_note(note.id, title="Renamed fixture", content_markdown="Changed fixture\n",
            location=note.location, expected_revision=note.revision)
        replay, created = self.service.import_greencloud_pending(proposal)
        self.assertFalse(created)
        self.assertEqual(replay, pending)

    def test_create_accepts_once_and_terminal_waits_for_matching_mirror(self) -> None:
        before = self._note_count()
        receipt, _created = self.service.import_greencloud_pending(
            self._submission("create", "create-once")
        )
        accepted = self._accept(receipt.id)
        self.assertEqual(accepted.outcome.kind, AcceptanceKind.SUCCESS)
        self.assertTrue(accepted.created)
        self.assertEqual(self._note_count(), before + 1)
        replay = self._accept(receipt.id)
        self.assertTrue(replay.already_processed)
        self.assertEqual(self._note_count(), before + 1)

        terminal = self.service.pending_greencloud_terminal_receipts()
        self.assertEqual(len(terminal), 1)
        item = terminal[0]
        self.assertEqual(item.cloud_request_id, "create-once")
        self.assertEqual(item.source_actor, "claude-code")
        self.assertEqual(item.terminal_state, "accepted")
        self.assertNotIn("content", item.receipt_payload)
        with self.assertRaisesRegex(ValueError, "先发布 actor mirror"):
            self.service.mark_greencloud_receipt_sent(item.receipt_id, item.fingerprint)
        self.assertTrue(
            self.service.mark_greencloud_mirror_published(item.receipt_id, item.fingerprint)
        )
        self.assertFalse(
            self.service.mark_greencloud_mirror_published(item.receipt_id, item.fingerprint)
        )
        refreshed = self.service.pending_greencloud_terminal_receipts()[0]
        self.assertTrue(refreshed.mirror_published)
        self.assertEqual(refreshed.fingerprint, item.fingerprint)
        self.assertTrue(
            self.service.mark_greencloud_receipt_sent(
                refreshed.receipt_id,
                refreshed.fingerprint,
            )
        )
        self.assertEqual(self.service.pending_greencloud_terminal_receipts(), ())
        self.assertFalse(
            self.service.mark_greencloud_receipt_sent(
                refreshed.receipt_id,
                refreshed.fingerprint,
            )
        )

    def test_redacted_append_accepts_and_redacted_edit_is_terminal_rejected(self) -> None:
        note = self._note(visibility="redacted")
        append, _created = self.service.import_greencloud_pending(
            self._submission(
                "append",
                "redacted-append",
                note_id=note.id,
                expected_version=note_version_token(note.id, note.updated_at),
                payload={"content": "safe tail"},
            )
        )
        accepted = self._accept(append.id)
        self.assertEqual(accepted.outcome.kind, AcceptanceKind.SUCCESS)
        self.assertIn("safe tail", accepted.note.content_markdown)

        current = self.notes.get_note(note.id)
        edit, _created = self.service.import_greencloud_pending(
            self._submission(
                "edit",
                "redacted-edit",
                note_id=note.id,
                expected_version=note_version_token(note.id, current.updated_at),
                payload={"edits": [{"old_markdown": "base", "new_markdown": "changed"}]},
            )
        )
        snapshot = self.notes.get_note(note.id)
        rejected = self._accept(edit.id)
        self.assertEqual(rejected.outcome.kind, AcceptanceKind.REJECTED)
        self.assertEqual(self.notes.get_note(note.id), snapshot)
        outcomes = {
            item.cloud_request_id: item.terminal_state
            for item in self.service.pending_greencloud_terminal_receipts()
        }
        self.assertEqual(outcomes["redacted-edit"], "rejected")

    def test_stale_and_revoked_acceptance_have_zero_formal_write(self) -> None:
        note = self._note()
        stale, _created = self.service.import_greencloud_pending(
            self._submission(
                "append",
                "stale",
                note_id=note.id,
                expected_version=VERSION,
                payload={"content": "tail"},
            )
        )
        snapshot = self.notes.get_note(note.id)
        conflict = self._accept(stale.id)
        self.assertEqual(conflict.outcome.kind, AcceptanceKind.CONFLICT)
        self.assertEqual(self.notes.get_note(note.id), snapshot)

        current = self.notes.get_note(note.id)
        revoked, _created = self.service.import_greencloud_pending(
            self._submission(
                "append",
                "revoked",
                actor="clawbot",
                note_id=note.id,
                expected_version=note_version_token(note.id, current.updated_at),
                payload={"content": "tail"},
            )
        )
        with self.connections.write() as connection:
            connection.execute(
                "DELETE FROM note_ai_targets WHERE note_id = ?",
                (note.id,),
            )
            connection.execute(
                "UPDATE notes SET ai_visibility = 'hidden' WHERE id = ?",
                (note.id,),
            )
        revoked_snapshot = self.notes.get_note(note.id)
        rejected = self._accept(revoked.id)
        self.assertEqual(rejected.outcome.kind, AcceptanceKind.REJECTED)
        self.assertEqual(self.notes.get_note(note.id), revoked_snapshot)

    def test_create_canonicalization_failure_is_terminal_and_zero_write(self) -> None:
        receipt, _created = self.service.import_greencloud_pending(
            self._submission("create", "canonical-failure")
        )
        before = self._note_count()
        with patch.object(
            self.canonicalizer,
            "canonicalize",
            side_effect=CanonicalizationError("fictional canonical failure"),
        ):
            result = self._accept(receipt.id)
        self.assertEqual(result.outcome.kind, AcceptanceKind.REJECTED)
        self.assertEqual(self._note_count(), before)
        persisted = self.service.get_receipt(receipt.id)
        self.assertEqual(persisted.status, "rejected")
        terminal = self.service.pending_greencloud_terminal_receipts()
        self.assertEqual(terminal[0].terminal_state, "rejected")

    def test_full_edit_bumps_revision_timestamp_and_history_once(self) -> None:
        note = self._note()
        receipt, _ = self.service.import_greencloud_pending(self._submission(
            "edit", "full-edit", note_id=note.id,
            expected_version=note_version_token(note.id, note.updated_at),
            payload={"edits": [{"old_markdown": "unique", "new_markdown": "changed"}]},
        ))
        result = self._accept(receipt.id)
        self.assertTrue(result.outcome.accepted)
        current = self.notes.get_note(note.id)
        self.assertEqual(current.content_markdown, "base changed\n")
        self.assertEqual(current.revision, note.revision + 1)
        self.assertNotEqual(current.updated_at, note.updated_at)
        self.assertEqual(self.canonicalizer.canonicalize_markdown(current.content_markdown), current.content_markdown)
        self._accept(receipt.id)
        with self.connections.read() as connection:
            count = connection.execute("SELECT COUNT(*) FROM ai_note_history WHERE request_id = ?", (receipt.request_id,)).fetchone()[0]
        self.assertEqual(count, 1)

    def test_edit_old_markdown_not_unique_is_conflict_and_zero_write(self) -> None:
        note = self._note()
        receipt, _ = self.service.import_greencloud_pending(self._submission(
            "edit", "nonunique-edit", note_id=note.id,
            expected_version=note_version_token(note.id, note.updated_at),
            payload={"edits": [{"old_markdown": "unique", "new_markdown": "changed"}]},
        ))
        # Exercise the unique-match guard independently of the version guard.
        with self.connections.write() as connection:
            connection.execute("UPDATE notes SET content_markdown = ? WHERE id = ?", ("unique then unique\n", note.id))
        before = self.notes.get_note(note.id)
        result = self._accept(receipt.id)
        self.assertEqual(result.outcome.kind, AcceptanceKind.CONFLICT)
        self.assertEqual(self.notes.get_note(note.id), before)
        self.assertEqual(self.service.pending_greencloud_terminal_receipts()[0].terminal_state, "conflict")

    def test_deleted_target_and_empty_canonical_create_are_terminal_zero_write(self) -> None:
        note = self._note()
        receipt, _ = self.service.import_greencloud_pending(self._submission(
            "append", "deleted-target", note_id=note.id,
            expected_version=note_version_token(note.id, note.updated_at), payload={"content": "tail"},
        ))
        self.notes.soft_delete_note(note.id)
        before = self.notes.get_note(note.id, include_deleted=True)
        self.assertEqual(self._accept(receipt.id).outcome.kind, AcceptanceKind.REJECTED)
        self.assertEqual(self.notes.get_note(note.id, include_deleted=True), before)
        create, _ = self.service.import_greencloud_pending(self._submission("create", "empty-canonical"))
        count = self._note_count()
        self.canonicalizer.canonicalize = lambda value: ""
        self.assertEqual(self._accept(create.id).outcome.kind, AcceptanceKind.REJECTED)
        self.assertEqual(self._note_count(), count)
        self.assertFalse(self.service.get_receipt(create.id).pending)

    def test_unsent_terminal_cannot_be_deleted_and_sent_rejection_can(self) -> None:
        receipt, _ = self.service.import_greencloud_pending(self._submission("create", "delete-guard"))
        self.service.discard_receipt(receipt.id)
        result = self.service.delete_processed_receipt(receipt.id)
        self.assertFalse(result.deleted)
        self.assertEqual(result.reason.value, "remote_sync_pending")
        item = self.service.pending_greencloud_terminal_receipts()[0]
        self.assertFalse(self.service._receipts.delete_if_unchanged(receipt.id, expected_status="discarded", expected_note_id=None))
        self.service.mark_greencloud_receipt_sent(item.receipt_id, item.fingerprint)
        self.assertTrue(self.service.delete_processed_receipt(receipt.id).deleted)

    def test_bulk_reject_is_exactly_one_source_and_actor(self) -> None:
        claude, _ = self.service.import_greencloud_pending(
            self._submission("create", "bulk-claude", actor="claude-code")
        )
        clawbot, _ = self.service.import_greencloud_pending(
            self._submission("create", "bulk-clawbot", actor="clawbot")
        )
        gale, _ = self.service.import_gale_submission(
            GaleReceiptImport(
                external_submission_id=claude.external_submission_id,
                idempotency_key="bulk-claude",
                title="Fictional Gale",
                body="Fictional Gale body",
                location=NoteLocation(),
                tags=(),
            )
        )
        result = self.service.reject_pending_by_actor(
            source=GREEN_CLOUD_SOURCE,
            actor_target_id="claude-code",
        )
        self.assertEqual(result.receipt_ids, (claude.id,))
        self.assertEqual(result.rejected_count, 1)
        self.assertEqual(self.service.get_receipt(claude.id).status, "discarded")
        self.assertTrue(self.service.get_receipt(clawbot.id).pending)
        self.assertTrue(self.service.get_receipt(gale.id).pending)
        with self.assertRaisesRegex(ValueError, "单一 GreenCloud"):
            self.service.reject_pending_by_actor(
                source="Gale",
                actor_target_id="claude-code",
            )


if __name__ == "__main__":
    unittest.main()
