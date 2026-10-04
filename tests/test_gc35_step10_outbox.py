from __future__ import annotations

import sqlite3
import tempfile
import unittest
from contextlib import closing
from pathlib import Path

from src.services.gale_submission_outbox import (
    CLAUDE_CODE_ACTOR,
    CLAWBOT_ACTOR,
    GENERALIZED_OUTBOX_SCHEMA_VERSION,
    OUTBOX_SCHEMA_VERSION,
    GaleSubmissionOutboxError,
    acknowledge_submission,
    create_submission,
    enqueue_proposal,
    export_unacknowledged,
    get_operation_status,
    initialize_outbox,
    record_terminal_receipt,
)


class GC35Step10OutboxTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory(
            prefix="snotes-gc35-step10-outbox-",
            dir="/private/tmp",
        )
        self.addCleanup(self.temporary.cleanup)
        self.outbox = Path(self.temporary.name) / "data" / "outbox.db"

    def test_generalized_schema_is_explicit_opt_in(self) -> None:
        initialize_outbox(self.outbox)
        with closing(sqlite3.connect(self.outbox)) as connection:
            self.assertEqual(
                connection.execute("PRAGMA user_version").fetchone()[0],
                OUTBOX_SCHEMA_VERSION,
            )
            columns = {
                row[1]
                for row in connection.execute(
                    "PRAGMA table_info(submissions)"
                ).fetchall()
            }
        self.assertNotIn("operation_type", columns)
        with self.assertRaisesRegex(
            GaleSubmissionOutboxError,
            "proposal_schema_required",
        ):
            enqueue_proposal(
                self.outbox,
                source_actor=CLAUDE_CODE_ACTOR,
                request_id="request-legacy",
                operation_type="create",
                payload={"title": "fictional"},
            )

    def test_opt_in_rebuild_preserves_legacy_gale_and_default_does_not_downgrade(
        self,
    ) -> None:
        initialize_outbox(self.outbox)
        original, _duplicate = create_submission(
            self.outbox,
            title="FICTIONAL_GALE_BEFORE_GENERALIZATION",
            content="Fictional Gale content before the local migration test.",
            idempotency_key="fictional-gale-before-generalization",
            ai_visibility="full",
            ai_targets=["gale"],
        )
        initialize_outbox(self.outbox, generalized=True)
        with closing(sqlite3.connect(self.outbox)) as connection:
            self.assertEqual(
                connection.execute("PRAGMA user_version").fetchone()[0],
                GENERALIZED_OUTBOX_SCHEMA_VERSION,
            )
            table_sql = str(
                connection.execute(
                    """
                    SELECT sql FROM sqlite_schema
                    WHERE type = 'table' AND name = 'submissions'
                    """
                ).fetchone()[0]
            )
        self.assertNotIn("operation_type IN", table_sql)
        restored = get_operation_status(
            self.outbox,
            source_actor="gale-staging",
            request_id="fictional-gale-before-generalization",
        )
        self.assertIsNotNone(restored)
        assert restored is not None
        self.assertEqual(restored.submission_uuid, original.submission_uuid)
        self.assertEqual(restored.operation_type, "create")
        self.assertEqual(restored.title, original.title)
        self.assertEqual(restored.operation_payload["ai_visibility"], "full")
        self.assertEqual(restored.operation_payload["ai_targets"], ["gale"])

        inode = self.outbox.stat().st_ino
        initialize_outbox(self.outbox)
        self.assertEqual(self.outbox.stat().st_ino, inode)
        with closing(sqlite3.connect(self.outbox)) as connection:
            self.assertEqual(
                connection.execute("PRAGMA user_version").fetchone()[0],
                GENERALIZED_OUTBOX_SCHEMA_VERSION,
            )

    def test_opt_in_rebuild_preserves_each_legacy_delivery_state(self) -> None:
        initialize_outbox(self.outbox)
        acknowledged, _duplicate = create_submission(
            self.outbox,
            title="FICTIONAL_ACKNOWLEDGED_BEFORE_REBUILD",
            content="Fictional acknowledged proposal.",
            idempotency_key="legacy-acknowledged",
            ai_visibility="full",
            main_category="Acknowledged",
        )
        export_unacknowledged(self.outbox)
        acknowledge_submission(
            self.outbox,
            submission_uuid=acknowledged.submission_uuid,
            local_receipt_id="101",
        )

        delivered, _duplicate = create_submission(
            self.outbox,
            title="FICTIONAL_DELIVERED_BEFORE_REBUILD",
            content="Fictional delivered proposal.",
            idempotency_key="legacy-delivered",
            ai_visibility="redacted",
            tags=["delivered"],
        )
        export_unacknowledged(self.outbox)
        queued, _duplicate = create_submission(
            self.outbox,
            title="FICTIONAL_QUEUED_BEFORE_REBUILD",
            content="Fictional queued proposal.",
            idempotency_key="legacy-queued",
            ai_targets=["gale"],
            sub_category="Queued",
        )

        initialize_outbox(self.outbox, generalized=True)
        restored = {
            request_id: get_operation_status(
                self.outbox,
                source_actor="gale-staging",
                request_id=request_id,
            )
            for request_id in (
                "legacy-acknowledged",
                "legacy-delivered",
                "legacy-queued",
            )
        }
        self.assertEqual(
            restored["legacy-acknowledged"].delivery_state,
            "acknowledged",
        )
        self.assertEqual(
            restored["legacy-acknowledged"].local_receipt_id,
            "101",
        )
        self.assertIsNotNone(restored["legacy-acknowledged"].delivered_at)
        self.assertIsNotNone(restored["legacy-acknowledged"].acknowledged_at)
        self.assertEqual(
            restored["legacy-acknowledged"].operation_payload["main_category"],
            "Acknowledged",
        )
        self.assertEqual(restored["legacy-delivered"].delivery_state, "delivered")
        self.assertIsNotNone(restored["legacy-delivered"].delivered_at)
        self.assertIsNone(restored["legacy-delivered"].acknowledged_at)
        self.assertEqual(
            restored["legacy-delivered"].operation_payload["tags"],
            ["delivered"],
        )
        self.assertEqual(restored["legacy-queued"].delivery_state, "queued")
        self.assertIsNone(restored["legacy-queued"].delivered_at)
        self.assertEqual(
            restored["legacy-queued"].operation_payload["ai_targets"],
            ["gale"],
        )

    def test_create_append_edit_and_future_operation_round_trip(self) -> None:
        initialize_outbox(self.outbox, generalized=True)
        cases = (
            ("create", None, None, {"title": "Fictional create", "markdown": "# A"}),
            ("append", 17, "snotes-v1-fictional", {"markdown": "tail"}),
            (
                "edit",
                "18",
                "snotes-v1-fictional-edit",
                {"edits": [{"old_markdown": "old", "new_markdown": "new"}]},
            ),
            ("prepend", 19, "snotes-v1-future", {"markdown": "head"}),
        )
        for index, (operation, note_id, version, payload) in enumerate(cases):
            with self.subTest(operation=operation):
                submission, duplicate = enqueue_proposal(
                    self.outbox,
                    source_actor=CLAUDE_CODE_ACTOR,
                    request_id=f"fictional-request-{index}",
                    operation_type=operation,
                    note_id=note_id,
                    expected_version=version,
                    payload=payload,
                )
                self.assertFalse(duplicate)
                self.assertEqual(submission.operation_type, operation)
                self.assertEqual(
                    submission.note_id,
                    str(note_id) if note_id is not None else None,
                )
                self.assertEqual(submission.expected_version, version)
                self.assertEqual(submission.operation_payload, payload)

    def test_actor_scoped_idempotency_precedes_queue_cap(self) -> None:
        initialize_outbox(self.outbox, generalized=True)
        first, duplicate = enqueue_proposal(
            self.outbox,
            source_actor=CLAUDE_CODE_ACTOR,
            request_id="同一-request",
            operation_type="create",
            payload={"title": "first"},
            queue_limit=1,
        )
        self.assertFalse(duplicate)
        repeated, repeated_duplicate = enqueue_proposal(
            self.outbox,
            source_actor=CLAUDE_CODE_ACTOR,
            request_id="同一-request",
            operation_type="edit",
            payload={"different": True},
            queue_limit=1,
        )
        self.assertTrue(repeated_duplicate)
        self.assertEqual(repeated.submission_uuid, first.submission_uuid)
        with self.assertRaisesRegex(GaleSubmissionOutboxError, "queue_full"):
            enqueue_proposal(
                self.outbox,
                source_actor=CLAUDE_CODE_ACTOR,
                request_id="second-request",
                operation_type="create",
                payload={"title": "second"},
                queue_limit=1,
            )

        other, other_duplicate = enqueue_proposal(
            self.outbox,
            source_actor=CLAWBOT_ACTOR,
            request_id="同一-request",
            operation_type="create",
            payload={"title": "other actor"},
            queue_limit=1,
        )
        self.assertFalse(other_duplicate)
        self.assertNotEqual(other.submission_uuid, first.submission_uuid)
        self.assertIsNone(
            get_operation_status(
                self.outbox,
                source_actor=CLAWBOT_ACTOR,
                request_id="second-request",
            )
        )
        for invalid_request in ("contains space", "x" * 129):
            with self.subTest(invalid_request=invalid_request[:20]):
                with self.assertRaisesRegex(
                    GaleSubmissionOutboxError,
                    "request_id_invalid",
                ):
                    enqueue_proposal(
                        self.outbox,
                        source_actor=CLAWBOT_ACTOR,
                        request_id=invalid_request,
                        operation_type="create",
                        payload={"title": "invalid request id"},
                    )

    def test_terminal_receipt_is_actor_scoped_and_releases_queue_slot(self) -> None:
        initialize_outbox(self.outbox, generalized=True)
        submission, _duplicate = enqueue_proposal(
            self.outbox,
            source_actor=CLAUDE_CODE_ACTOR,
            request_id="terminal-request",
            operation_type="append",
            note_id=42,
            expected_version="snotes-v1-terminal",
            payload={"markdown": "fictional tail"},
            queue_limit=1,
        )
        exported = export_unacknowledged(self.outbox)
        self.assertEqual(
            [item.submission_uuid for item in exported],
            [submission.submission_uuid],
        )
        acknowledge_submission(
            self.outbox,
            submission_uuid=submission.submission_uuid,
            local_receipt_id="42",
        )
        terminal, duplicate = record_terminal_receipt(
            self.outbox,
            source_actor=CLAUDE_CODE_ACTOR,
            request_id="terminal-request",
            terminal_state="accepted",
            receipt_payload={"note_id": 42, "version": "snotes-v1-new"},
        )
        self.assertFalse(duplicate)
        self.assertEqual(terminal.delivery_state, "accepted")
        acknowledged_again, ack_duplicate = acknowledge_submission(
            self.outbox,
            submission_uuid=submission.submission_uuid,
            local_receipt_id="42",
        )
        self.assertTrue(ack_duplicate)
        self.assertEqual(acknowledged_again.delivery_state, "accepted")
        repeated, repeated_duplicate = record_terminal_receipt(
            self.outbox,
            source_actor=CLAUDE_CODE_ACTOR,
            request_id="terminal-request",
            terminal_state="accepted",
            receipt_payload={"version": "snotes-v1-new", "note_id": 42},
        )
        self.assertTrue(repeated_duplicate)
        self.assertEqual(repeated.submission_uuid, submission.submission_uuid)
        with self.assertRaisesRegex(
            GaleSubmissionOutboxError,
            "terminal_receipt_conflict",
        ):
            record_terminal_receipt(
                self.outbox,
                source_actor=CLAUDE_CODE_ACTOR,
                request_id="terminal-request",
                terminal_state="rejected",
            )

        next_item, _duplicate = enqueue_proposal(
            self.outbox,
            source_actor=CLAUDE_CODE_ACTOR,
            request_id="after-terminal",
            operation_type="create",
            payload={"title": "slot released"},
            queue_limit=1,
        )
        self.assertEqual(next_item.delivery_state, "queued")


if __name__ == "__main__":
    unittest.main()
