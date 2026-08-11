from __future__ import annotations

import tempfile
import unittest
import uuid
import json
from pathlib import Path

from src.snotes.application.ai_access_metadata_service import AiAccessMetadataService
from src.snotes.application.ai_receipt_inbox_service import (
    AiReceiptAcceptFields,
    AiReceiptInboxService,
    ReceiptDeleteReason,
    UnsupportedReceiptOperationError,
)
from src.snotes.application.note_service import NoteService
from src.snotes.application.tag_service import TagService
from src.snotes.domain.models import NoteLocation
from src.snotes.storage.repositories.ai_receipts import (
    SQLiteAiReceiptInboxRepository,
)
from src.snotes.storage.repositories.directories import SQLiteDirectoriesRepository
from src.snotes.storage.repositories.notes import SQLiteNotesRepository
from src.snotes.storage.repositories.tags import SQLiteTagsRepository
from src.snotes.storage.schema import initialize_database


class DeterministicUuidFactory:
    def __init__(self, start: int) -> None:
        self.value = start

    def __call__(self) -> uuid.UUID:
        self.value += 1
        return uuid.UUID(int=self.value, version=4)


class LinkReceiptBeforeDeleteRepository(SQLiteAiReceiptInboxRepository):
    """Test adapter that simulates a receipt becoming linked after its read."""

    def __init__(self, connections, directories, tags, note_id: int) -> None:
        super().__init__(connections, directories, tags)
        self.note_id = note_id

    def delete_if_unchanged(
        self,
        receipt_id: int,
        *,
        expected_status: str,
        expected_note_id: int | None,
    ) -> bool:
        with self.connections.write() as connection:
            connection.execute(
                """
                UPDATE ai_receipt_inbox
                SET status = 'accepted', note_id = ?
                WHERE id = ?
                """,
                (self.note_id, receipt_id),
            )
        return super().delete_if_unchanged(
            receipt_id,
            expected_status=expected_status,
            expected_note_id=expected_note_id,
        )


class Package4AiReceiptInboxCoreTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory(
            prefix="snotes-r2-package4-receipt-inbox-"
        )
        self.addCleanup(self.temporary.cleanup)
        self.database_path = Path(self.temporary.name) / "fictional.db"
        self.connections = initialize_database(self.database_path)
        directories = SQLiteDirectoriesRepository(self.connections)
        tags_repository = SQLiteTagsRepository(self.connections)
        notes_repository = SQLiteNotesRepository(
            self.connections,
            directories,
            uuid_factory=DeterministicUuidFactory(80),
        )
        self.notes = NoteService(notes_repository)
        self.tags = TagService(tags_repository)
        self.metadata = AiAccessMetadataService(notes_repository)
        receipt_repository = SQLiteAiReceiptInboxRepository(
            self.connections,
            directories,
            tags_repository,
            uuid_factory=DeterministicUuidFactory(180),
        )
        self.receipts = AiReceiptInboxService(
            receipt_repository,
            notes=self.notes,
            tags=self.tags,
            ai_metadata=self.metadata,
        )
        created = self.notes.create_note(NoteLocation("项目", "虚构"))
        self.note = self.notes.save_note(
            created.id,
            title="Fictional audit note",
            content_html="<p>FICTIONAL_NOTE_BODY_CANARY</p>",
            location=created.location,
            expected_revision=created.revision,
        )
        self.metadata.set_note_visibility(self.note.id, "full")
        self.metadata.set_note_targets(self.note.id, ("gale",))

    def test_pending_reads_only_pending_in_r1_order(self) -> None:
        self._insert_receipt(1, status="pending", updated_at="2026-08-10T10:00:00")
        self._insert_receipt(2, status="accepted", updated_at="2026-08-10T12:00:00")
        self._insert_receipt(3, status="pending", updated_at="2026-08-10T11:00:00")
        self._insert_receipt(4, status="pending", updated_at="2026-08-10T11:00:00")

        pending = self.receipts.list_pending_receipts()

        self.assertEqual([item.id for item in pending], [4, 3, 1])
        self.assertEqual({item.status for item in pending}, {"pending"})

    def test_processed_reads_every_non_pending_status_in_r1_order(self) -> None:
        self._insert_receipt(1, status="pending", updated_at="2026-08-10T13:00:00")
        self._insert_receipt(2, status="accepted", updated_at="2026-08-10T09:00:00")
        self._insert_receipt(3, status="accepted", updated_at="2026-08-10T10:00:00")
        self._insert_receipt(4, status="discarded", updated_at="2026-08-10T12:00:00")
        self._insert_receipt(5, status="rejected", updated_at="2026-08-10T12:00:00")
        self._insert_receipt(6, status="legacy-processed", updated_at="2026-08-10T14:00:00")

        processed = self.receipts.list_processed_receipts()

        self.assertEqual([item.id for item in processed], [3, 2, 5, 4, 6])
        self.assertNotIn("pending", {item.status for item in processed})

    def test_get_one_returns_only_the_structured_ai_center_record(self) -> None:
        self._insert_receipt(
            41,
            status="discarded",
            title="Fictional receipt title",
            body="FICTIONAL_RECEIPT_BODY",
            request_id="fictional-request-41",
            external_submission_id="fictional-external-41",
            idempotency_key="fictional-key-41",
        )

        receipt = self.receipts.get_receipt(41)

        self.assertIsNotNone(receipt)
        self.assertEqual(receipt.id, 41)
        self.assertEqual(receipt.title, "Fictional receipt title")
        self.assertEqual(receipt.body, "FICTIONAL_RECEIPT_BODY")
        self.assertEqual(receipt.status, "discarded")
        self.assertEqual(receipt.request_id, "fictional-request-41")
        self.assertEqual(receipt.external_submission_id, "fictional-external-41")
        self.assertEqual(receipt.idempotency_key, "fictional-key-41")
        self.assertIsNone(self.receipts.get_receipt(999))

    def test_every_r1_deletable_processed_status_uses_one_service_entry(self) -> None:
        for receipt_id, status in enumerate(
            ("accepted", "discarded", "rejected", "legacy-processed"),
            start=10,
        ):
            with self.subTest(status=status):
                self._insert_receipt(receipt_id, status=status)
                result = self.receipts.delete_processed_receipt(receipt_id)
                self.assertTrue(result.deleted)
                self.assertEqual(result.reason, ReceiptDeleteReason.DELETED)
                self.assertIsNone(self.receipts.get_receipt(receipt_id))

    def test_accepted_linked_receipt_is_retained_with_business_reason(self) -> None:
        self._insert_receipt(51, status="accepted", note_id=self.note.id)
        note_before = self._note_state()

        result = self.receipts.delete_processed_receipt(51)

        self.assertFalse(result.deleted)
        self.assertEqual(
            result.reason,
            ReceiptDeleteReason.ACCEPTED_LINKED_NOTE,
        )
        self.assertIn("保留用于查账", result.message)
        self.assertIsNotNone(self.receipts.get_receipt(51))
        self.assertEqual(self._note_state(), note_before)

    def test_allowed_receipt_delete_does_not_mutate_note_or_ai_metadata(self) -> None:
        self._insert_receipt(61, status="discarded", note_id=self.note.id)
        note_before = self._note_state()

        result = self.receipts.delete_processed_receipt(61)

        self.assertTrue(result.deleted)
        self.assertIsNone(self.receipts.get_receipt(61))
        self.assertEqual(self._note_state(), note_before)

    def test_new_accepted_note_link_cannot_be_bypassed_between_read_and_delete(self) -> None:
        self._insert_receipt(62, status="accepted")
        racing_service = AiReceiptInboxService(
            LinkReceiptBeforeDeleteRepository(
                self.connections,
                SQLiteDirectoriesRepository(self.connections),
                SQLiteTagsRepository(self.connections),
                self.note.id,
            )
        )
        note_before = self._note_state()

        result = racing_service.delete_processed_receipt(62)

        self.assertFalse(result.deleted)
        self.assertEqual(result.reason, ReceiptDeleteReason.CONCURRENT_CHANGE)
        retained = self.receipts.get_receipt(62)
        self.assertIsNotNone(retained)
        self.assertEqual(retained.status, "accepted")
        self.assertEqual(retained.note_id, self.note.id)
        self.assertEqual(self._note_state(), note_before)

    def test_pending_and_missing_receipts_are_not_deleted_by_processed_entry(self) -> None:
        self._insert_receipt(71, status="pending")

        pending = self.receipts.delete_processed_receipt(71)
        missing = self.receipts.delete_processed_receipt(999)

        self.assertEqual(pending.reason, ReceiptDeleteReason.NOT_PROCESSED)
        self.assertEqual(missing.reason, ReceiptDeleteReason.NOT_FOUND)
        self.assertIsNotNone(self.receipts.get_receipt(71))

    def test_accept_creates_and_links_one_note_with_edited_r1_fields(self) -> None:
        self._insert_receipt(
            81,
            status="pending",
            title="Original title",
            body="Original body",
        )
        before_count = len(self.notes.list_active_notes())
        fields = AiReceiptAcceptFields(
            title="Accepted fictional note",
            body="Line one\n<script>fictional</script>",
            main_category="项目",
            sub_category="验收",
            tags="One, #two，one",
            visibility="AI脱敏后可见",
            target_ids=("common", "claude-code", "codex"),
        )

        accepted = self.receipts.accept_receipt(81, fields)
        repeated = self.receipts.accept_receipt(81, fields)

        self.assertTrue(accepted.created)
        self.assertFalse(accepted.already_processed)
        self.assertFalse(repeated.created)
        self.assertTrue(repeated.already_processed)
        self.assertEqual(repeated.note.id, accepted.note.id)
        self.assertEqual(len(self.notes.list_active_notes()), before_count + 1)
        note = self.notes.get_note(accepted.note.id)
        self.assertEqual(note.title, "Accepted fictional note")
        self.assertEqual(
            note.content_html,
            "<p>Line one<br>&lt;script&gt;fictional&lt;/script&gt;</p>",
        )
        self.assertEqual(note.location, NoteLocation("项目", "验收"))
        self.assertEqual(self.tags.get_note_tags(note.id), ["one", "two"])
        metadata = self.metadata.get_note_metadata(note.id)
        self.assertEqual(metadata.visibility, "redacted")
        self.assertEqual(
            metadata.target_ids,
            ("common", "claude-code", "codex"),
        )
        receipt = self.receipts.get_receipt(81)
        self.assertEqual(receipt.status, "accepted")
        self.assertEqual(receipt.note_id, note.id)

    def test_accept_failure_rolls_back_note_metadata_directory_and_receipt(self) -> None:
        self._insert_receipt(82, status="pending")
        before = self._database_counts()
        with self.connections.write() as connection:
            connection.execute(
                """
                CREATE TRIGGER fictional_accept_failure
                BEFORE UPDATE OF status ON ai_receipt_inbox
                WHEN NEW.id = 82 AND NEW.status = 'accepted'
                BEGIN
                    SELECT RAISE(ABORT, 'fictional accept failure');
                END
                """
            )

        with self.assertRaisesRegex(Exception, "fictional accept failure"):
            self.receipts.accept_receipt(
                82,
                AiReceiptAcceptFields(
                    title="Must rollback",
                    body="FICTIONAL_ROLLBACK_BODY",
                    main_category="Rollback main",
                    sub_category="Rollback sub",
                    tags="rollback-tag",
                    visibility="full",
                    target_ids=("gale",),
                ),
            )

        self.assertEqual(self._database_counts(), before)
        retained = self.receipts.get_receipt(82)
        self.assertEqual(retained.status, "pending")
        self.assertIsNone(retained.note_id)

    def test_discard_is_idempotent_and_never_mutates_notes_or_trash(self) -> None:
        self._insert_receipt(83, status="pending")
        note_before = self._note_state()
        counts_before = self._database_counts()

        discarded = self.receipts.discard_receipt(83)
        repeated = self.receipts.discard_receipt(83)

        self.assertTrue(discarded.changed)
        self.assertFalse(discarded.already_processed)
        self.assertFalse(repeated.changed)
        self.assertTrue(repeated.already_processed)
        self.assertEqual(repeated.receipt.status, "discarded")
        self.assertEqual(self._note_state(), note_before)
        after = self._database_counts()
        self.assertEqual(after["notes"], counts_before["notes"])
        self.assertEqual(after["trash"], counts_before["trash"])

    def test_request_id_accept_uses_default_or_single_edited_metadata(self) -> None:
        cases = (
            (86, "full", ("gale",), "full", ("gale",)),
            (87, "redacted", ("gale",), "redacted", ("gale",)),
            (88, "full", ("ke-den",), "full", ("ke-den",)),
        )
        for receipt_id, visibility, targets, expected_visibility, expected_targets in cases:
            with self.subTest(receipt_id=receipt_id):
                self._insert_receipt(
                    receipt_id,
                    status="pending",
                    request_id=f"fictional-create-request-{receipt_id}",
                    actor_target_id="gale",
                    operation_type="create",
                )
                accepted = self.receipts.accept_receipt(
                    receipt_id,
                    AiReceiptAcceptFields(
                        title=f"Request-id note {receipt_id}",
                        body="Request-id body",
                        main_category="项目",
                        sub_category="虚构",
                        tags="request-id",
                        visibility=visibility,
                        target_ids=targets,
                    ),
                )

                metadata = self.metadata.get_note_metadata(accepted.note.id)
                self.assertEqual(metadata.visibility, expected_visibility)
                self.assertEqual(metadata.target_ids, expected_targets)
                receipt = self.receipts.get_receipt(receipt_id)
                self.assertEqual(receipt.status, "accepted")
                self.assertEqual(receipt.note_id, accepted.note.id)

    def test_request_id_create_accept_preserves_edited_metadata_and_idempotency(self) -> None:
        self._insert_receipt(
            84,
            status="pending",
            request_id="fictional-create-request-84",
            actor_target_id="gale",
            operation_type="create",
        )
        with self.connections.write() as connection:
            connection.execute(
                """
                INSERT INTO ai_operations (
                    request_id, actor_target_id, operation_type, status,
                    result_json, created_at, updated_at
                ) VALUES (?, 'gale', 'create', 'pending_confirmation', ?, ?, ?)
                """,
                (
                    "fictional-create-request-84",
                    json.dumps({"status": "pending_confirmation"}),
                    "2026-08-10T07:00:00",
                    "2026-08-10T07:00:00",
                ),
            )
        before_count = len(self.notes.list_active_notes())
        fields = AiReceiptAcceptFields(
            title="Request-id note",
            body="Request-id body",
            main_category="项目",
            sub_category="虚构",
            tags="request-id",
            visibility="redacted",
            target_ids=("ke-den",),
        )

        first = self.receipts.accept_receipt(84, fields)
        second = self.receipts.accept_receipt(84, fields)

        self.assertEqual(first.note.id, second.note.id)
        self.assertEqual(len(self.notes.list_active_notes()), before_count + 1)
        metadata = self.metadata.get_note_metadata(first.note.id)
        self.assertEqual(metadata.visibility, "redacted")
        self.assertEqual(metadata.target_ids, ("ke-den",))
        with self.connections.read() as connection:
            operation = connection.execute(
                "SELECT status, result_json FROM ai_operations WHERE request_id = ?",
                ("fictional-create-request-84",),
            ).fetchone()
        self.assertEqual(operation["status"], "created")
        self.assertEqual(json.loads(operation["result_json"])["note_id"], first.note.id)

    def test_existing_note_proposal_remains_out_of_scope_without_mutation(self) -> None:
        self._insert_receipt(
            85,
            status="pending",
            request_id="fictional-append-request-85",
            actor_target_id="gale",
            operation_type="append",
        )
        note_before = self._note_state()

        with self.assertRaises(UnsupportedReceiptOperationError):
            self.receipts.accept_receipt(
                85,
                AiReceiptAcceptFields(
                    title="Ignored",
                    body="Ignored",
                    main_category="项目",
                    sub_category="虚构",
                    tags="",
                    visibility="full",
                    target_ids=("gale",),
                ),
            )

        self.assertEqual(self._note_state(), note_before)
        self.assertEqual(self.receipts.get_receipt(85).status, "pending")

    def _insert_receipt(
        self,
        receipt_id: int,
        *,
        status: str,
        title: str = "Fictional receipt",
        body: str = "FICTIONAL_RECEIPT_BODY",
        updated_at: str = "2026-08-10T08:00:00",
        note_id: int | None = None,
        request_id: str | None = None,
        external_submission_id: str | None = None,
        idempotency_key: str | None = None,
        actor_target_id: str | None = None,
        operation_type: str | None = None,
    ) -> None:
        with self.connections.write() as connection:
            connection.execute(
                """
                INSERT INTO ai_receipt_inbox (
                    id, raw_text, parse_status, status, source, action, title,
                    main_category, sub_category, tags,
                    ai_visibility, ai_targets, body, note_id, created_at,
                    updated_at, parse_error, request_id,
                    actor_target_id, operation_type,
                    external_submission_id, idempotency_key
                ) VALUES (
                    ?, 'FICTIONAL_RAW_RECEIPT', 'parsed', ?, 'Fictional AI',
                    '新增', ?, '项目', '虚构', 'fictional-tag', 'full',
                    'gale', ?, ?, '2026-08-10T07:00:00', ?, NULL, ?, ?, ?, ?, ?
                )
                """,
                (
                    receipt_id,
                    status,
                    title,
                    body,
                    note_id,
                    updated_at,
                    request_id,
                    actor_target_id,
                    operation_type,
                    external_submission_id,
                    idempotency_key,
                ),
            )

    def _note_state(self) -> tuple[tuple, tuple]:
        with self.connections.read() as connection:
            note = connection.execute(
                "SELECT * FROM notes WHERE id = ?",
                (self.note.id,),
            ).fetchone()
            targets = connection.execute(
                """
                SELECT target_id, created_at
                FROM note_ai_targets
                WHERE note_id = ?
                ORDER BY target_id
                """,
                (self.note.id,),
            ).fetchall()
        return tuple(note), tuple(tuple(row) for row in targets)

    def _database_counts(self) -> dict[str, int]:
        with self.connections.read() as connection:
            return {
                "notes": int(connection.execute("SELECT COUNT(*) FROM notes").fetchone()[0]),
                "trash": int(
                    connection.execute(
                        "SELECT COUNT(*) FROM notes WHERE is_deleted = 1"
                    ).fetchone()[0]
                ),
                "directories": int(
                    connection.execute("SELECT COUNT(*) FROM directories").fetchone()[0]
                ),
                "tags": int(connection.execute("SELECT COUNT(*) FROM tags").fetchone()[0]),
                "targets": int(
                    connection.execute("SELECT COUNT(*) FROM note_ai_targets").fetchone()[0]
                ),
            }


if __name__ == "__main__":
    unittest.main()
