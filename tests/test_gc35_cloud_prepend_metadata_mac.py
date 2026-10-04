"""Mac authority coverage for Cloud prepend and metadata proposals."""

from __future__ import annotations

import json
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
from src.snotes.domain.greencloud import GreenCloudError, GreenCloudSubmission
from src.snotes.domain.models import NoteLocation
from src.snotes.storage.repositories.ai_mcp_operations import (
    SQLiteAiMcpOperationRepository,
)
from src.snotes.storage.repositories.ai_receipts import SQLiteAiReceiptInboxRepository
from src.snotes.storage.repositories.directories import SQLiteDirectoriesRepository
from src.snotes.storage.repositories.notes import SQLiteNotesRepository
from src.snotes.storage.repositories.tags import SQLiteTagsRepository
from src.snotes.storage.schema import initialize_database
from src.snotes.transport.greencloud.outbox import parse_export_payload
from tests.canonicalizer_test_double import CanonicalizerTestDouble


VERSION = "snotes-v1-" + "a" * 64


class CloudPrependMetadataMacTests(unittest.TestCase):
    def setUp(self) -> None:
        temporary = tempfile.TemporaryDirectory(prefix="snotes-cloud-new-ops-")
        self.addCleanup(temporary.cleanup)
        self.connections = initialize_database(Path(temporary.name) / "fictional.db")
        directory_repository = SQLiteDirectoriesRepository(self.connections)
        tag_repository = SQLiteTagsRepository(self.connections)
        note_repository = SQLiteNotesRepository(self.connections, directory_repository)
        self.notes = NoteService(note_repository)
        self.tags = TagService(tag_repository)
        self.metadata = AiAccessMetadataService(note_repository)
        self.directories = DirectoryService(directory_repository)
        self.canonicalizer = CanonicalizerTestDouble(
            lambda value: value.rstrip() + "\n" if value.strip() else ""
        )
        self.service = AiReceiptInboxService(
            SQLiteAiReceiptInboxRepository(
                self.connections, directory_repository, tag_repository
            ),
            notes=self.notes,
            tags=self.tags,
            ai_metadata=self.metadata,
            mcp_operations=SQLiteAiMcpOperationRepository(
                self.connections, tag_repository
            ),
            canonicalizer=self.canonicalizer,
            authorization=AiWriteAuthorization(directories=self.directories),
        )
        destination = self.notes.create_note(NoteLocation("Projects", "Cloud"))
        self.destination_id = destination.directory_id

    def _note(self, body: str = "base body\n"):
        blank = self.notes.create_note(NoteLocation())
        note = self.notes.save_note(
            blank.id,
            title="Fictional target",
            content_markdown=body,
            location=blank.location,
            expected_revision=blank.revision,
        )
        self.metadata.set_note_visibility(note.id, "full")
        self.metadata.set_note_targets(note.id, ("claude-code",))
        return self.notes.get_note(note.id)

    def _submission(
        self,
        operation_type: str,
        request_id: str,
        note,
        payload: dict,
        *,
        expected_version: str | None = None,
    ) -> GreenCloudSubmission:
        return GreenCloudSubmission(
            submission_uuid=f"uuid-{request_id}",
            source_actor="claude-code",
            request_id=request_id,
            operation_type=operation_type,
            note_id=note.id,
            expected_version=(
                expected_version
                if expected_version is not None
                else note_version_token(note.id, note.updated_at)
            ),
            payload=payload,
            created_at="2026-10-04T00:00:00Z",
            delivery_state="delivered",
            delivered_at="2026-10-04T00:00:01Z",
        )

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
                target_ids=(),
            ),
        )

    def test_transport_accepts_only_the_two_new_allowlisted_operation_names(self) -> None:
        def envelope(operation_type: str, payload: dict) -> bytes:
            return json.dumps(
                {
                    "status": "ok",
                    "source": "greencloud",
                    "schema_version": 3,
                    "submissions": [
                        {
                            "submission_uuid": "00000000-0000-4000-8000-000000000612",
                            "source_actor": "claude-code",
                            "request_id": "fictional-new-operation",
                            "operation_type": operation_type,
                            "note_id": "42",
                            "expected_version": VERSION,
                            "payload": payload,
                            "created_at": "2026-10-04T00:00:00Z",
                            "delivery_state": "delivered",
                            "delivered_at": "2026-10-04T00:00:01Z",
                        }
                    ],
                }
            ).encode()

        for operation, payload in (
            ("prepend", {"content": "head"}),
            ("update_note_metadata", {}),
        ):
            with self.subTest(operation=operation):
                self.assertEqual(
                    parse_export_payload(envelope(operation, payload))[0].operation_type,
                    operation,
                )
        with self.assertRaisesRegex(GreenCloudError, "operation_type_invalid"):
            parse_export_payload(envelope("delete", {}))

    def test_prepend_accepts_once_through_existing_canonical_owner(self) -> None:
        note = self._note()
        receipt, created = self.service.import_greencloud_pending(
            self._submission(
                "prepend", "prepend-once", note, {"content": "cloud head"}
            )
        )
        self.assertTrue(created)
        self.assertEqual(receipt.operation_type, "prepend")
        result = self._accept(receipt.id)
        self.assertEqual(result.outcome.kind, AcceptanceKind.SUCCESS)
        current = self.notes.get_note(note.id)
        self.assertEqual(current.content_markdown, "cloud head\n\nbase body\n")
        self.assertEqual(current.content_markdown.count("cloud head"), 1)
        self.assertEqual(current.revision, note.revision + 1)
        self.assertNotEqual(current.updated_at, note.updated_at)
        replay = self._accept(receipt.id)
        self.assertTrue(replay.already_processed)
        self.assertEqual(self.notes.get_note(note.id), current)

    def test_metadata_keeps_public_payload_and_never_canonicalizes_body(self) -> None:
        note = self._note("legacy body without final newline")
        self.tags.set_note_tags(note.id, ("base", "remove"))
        note = self.notes.get_note(note.id)
        original_body = note.content_markdown
        payload = {
            "title": "Renamed by Cloud",
            "directory_id": self.destination_id,
            "add_tags": ["#New", "new"],
            "remove_tags": ["remove"],
        }
        submission = self._submission(
            "update_note_metadata", "metadata-accept", note, payload
        )
        receipt, _created = self.service.import_greencloud_pending(
            submission
        )
        self.assertEqual(receipt.operation_type, "update_note_metadata")
        with self.connections.read() as connection:
            row = connection.execute(
                "SELECT operation_type, operation_payload FROM ai_receipt_inbox WHERE id=?",
                (receipt.id,),
            ).fetchone()
        self.assertEqual(row["operation_type"], "update_note_metadata")
        self.assertEqual(json.loads(row["operation_payload"]), payload)
        preview = self.service.pending_metadata_display_changes(receipt.id)
        self.assertEqual(preview["title"], "Renamed by Cloud")
        self.assertEqual(preview["directory"], ("Projects", "Cloud"))
        self.assertEqual(preview["added_tags"], ("New",))
        self.assertEqual(preview["removed_tags"], ("remove",))

        with patch.object(
            self.canonicalizer,
            "canonicalize",
            side_effect=AssertionError("metadata must not canonicalize body"),
        ):
            result = self._accept(receipt.id)
        self.assertEqual(result.outcome.kind, AcceptanceKind.SUCCESS)
        current = self.notes.get_note(note.id)
        self.assertEqual(current.title, "Renamed by Cloud")
        self.assertEqual(current.location, NoteLocation("Projects", "Cloud"))
        self.assertEqual(set(self.tags.get_note_tags(note.id)), {"base", "new"})
        self.assertEqual(current.content_markdown, original_body)
        self.assertEqual(current.revision, note.revision + 1)
        access = self.metadata.get_note_metadata(note.id)
        self.assertEqual(access.visibility, "full")
        self.assertEqual(access.target_ids, ("claude-code",))
        terminal = self.service.pending_greencloud_terminal_receipts()[0]
        self.assertEqual(terminal.cloud_request_id, "metadata-accept")
        self.assertEqual(terminal.terminal_state, "accepted")
        replay, replay_created = self.service.import_greencloud_pending(submission)
        self.assertFalse(replay_created)
        self.assertEqual(replay.id, receipt.id)
        with self.connections.read() as connection:
            operation_type = connection.execute(
                "SELECT operation_type FROM ai_operations WHERE request_id=?",
                (receipt.request_id,),
            ).fetchone()[0]
        self.assertEqual(operation_type, "update_note_metadata")

    def test_stale_and_revoked_new_operations_are_zero_write(self) -> None:
        stale_note = self._note()
        stale, _created = self.service.import_greencloud_pending(
            self._submission(
                "prepend",
                "prepend-stale",
                stale_note,
                {"content": "must not appear"},
                expected_version=VERSION,
            )
        )
        stale_snapshot = self.notes.get_note(stale_note.id)
        self.assertEqual(self._accept(stale.id).outcome.kind, AcceptanceKind.CONFLICT)
        self.assertEqual(self.notes.get_note(stale_note.id), stale_snapshot)

        revoked_note = self._note()
        revoked, _created = self.service.import_greencloud_pending(
            self._submission(
                "update_note_metadata",
                "metadata-revoked",
                revoked_note,
                {"title": "must not apply"},
            )
        )
        with self.connections.write() as connection:
            connection.execute(
                "DELETE FROM note_ai_targets WHERE note_id = ?", (revoked_note.id,)
            )
            connection.execute(
                "UPDATE notes SET ai_visibility = 'hidden' WHERE id = ?",
                (revoked_note.id,),
            )
        revoked_snapshot = self.notes.get_note(revoked_note.id)
        result = self._accept(revoked.id)
        self.assertEqual(result.outcome.kind, AcceptanceKind.REJECTED)
        self.assertEqual(self.notes.get_note(revoked_note.id), revoked_snapshot)

    def test_metadata_rejects_fields_outside_the_public_local_allowlist(self) -> None:
        note = self._note()
        for payload in (
            {"ai_visibility": "hidden"},
            {"actor_targets": ["clawbot"]},
            {"delete": True},
        ):
            with self.subTest(payload=payload), self.assertRaisesRegex(
                ValueError, "GreenCloud 资料提案内容无效"
            ):
                self.service.import_greencloud_pending(
                    self._submission(
                        "update_note_metadata",
                        "blocked-field-" + next(iter(payload)),
                        note,
                        payload,
                    )
                )

        with self.assertRaisesRegex(ValueError, "GreenCloud 资料提案内容无效"):
            self.service.import_greencloud_pending(
                self._submission(
                    "update_note_metadata",
                    "empty-tags",
                    note,
                    {"add_tags": []},
                )
            )

        nullable, _ = self.service.import_greencloud_pending(
            self._submission(
                "update_note_metadata",
                "nullable-fields",
                note,
                {
                    "title": "Nullable title",
                    "directory_id": None,
                    "add_tags": None,
                    "remove_tags": None,
                },
            )
        )
        self.assertEqual(self._accept(nullable.id).outcome.kind, AcceptanceKind.SUCCESS)
        self.assertEqual(self.notes.get_note(note.id).title, "Nullable title")

    def test_empty_metadata_payload_pulls_and_finishes_as_zero_write(self) -> None:
        note = self._note("body must remain byte-identical")
        proposal = self._submission(
            "update_note_metadata", "metadata-no-change", note, {}
        )
        receipt, created = self.service.import_greencloud_pending(proposal)
        self.assertTrue(created)
        self.assertEqual(self.service.pending_metadata_display_changes(receipt.id), {})
        with self.connections.read() as connection:
            stored_payload = connection.execute(
                "SELECT operation_payload FROM ai_receipt_inbox WHERE id=?",
                (receipt.id,),
            ).fetchone()[0]
        self.assertEqual(json.loads(stored_payload), {})

        snapshot = self.notes.get_note(note.id)
        result = self._accept(receipt.id)
        self.assertEqual(result.outcome.kind, AcceptanceKind.REJECTED)
        self.assertEqual(result.outcome.reason, "请求没有产生实际变化。")
        self.assertEqual(self.notes.get_note(note.id), snapshot)
        processed = self.service.get_receipt(receipt.id)
        self.assertEqual(processed.status, "rejected")
        terminal = self.service.pending_greencloud_terminal_receipts()[0]
        self.assertEqual(terminal.cloud_request_id, "metadata-no-change")
        self.assertEqual(terminal.terminal_state, "rejected")
        self.assertFalse(terminal.mirror_published)

        replay, replay_created = self.service.import_greencloud_pending(proposal)
        self.assertFalse(replay_created)
        self.assertEqual(replay.id, receipt.id)


if __name__ == "__main__":
    unittest.main()
