from __future__ import annotations

import sqlite3
import stat
import tempfile
import unittest
from pathlib import Path

from src.services.gale_submission_outbox import (
    GALE_STAGING_ACTOR,
    OUTBOX_SCHEMA_VERSION,
    GaleSubmissionOutboxError,
    acknowledge_submission,
    create_submission,
    export_unacknowledged,
    initialize_outbox,
    outbox_status,
)


class PackageDRemoteStagingTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory(
            prefix="snotes-r1b-remote-tests-",
            dir="/private/tmp",
        )
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.outbox = self.root / "oracle" / "outbox.db"
        initialize_outbox(self.outbox)

    def create_fictional(
        self,
        suffix: str = "001",
        *,
        idempotency_key: str | None = None,
    ):
        return create_submission(
            self.outbox,
            title=f"FICTIONAL_REMOTE_GALE_SUBMISSION_{suffix}",
            content=(
                f"A fictional Oracle outbox submission for R1B staging {suffix}."
            ),
            suggested_directory="虚构测试/Gale 投稿/待确认",
            suggested_tags=["虚构", "R1B"],
            idempotency_key=(
                idempotency_key or f"fictional-remote-r1b-{suffix}"
            ),
            source_actor=GALE_STAGING_ACTOR,
        )

    def test_outbox_schema_initialization_is_transactional_and_repeatable(
        self,
    ) -> None:
        before = self.outbox.stat()
        initialize_outbox(self.outbox)
        after = self.outbox.stat()
        self.assertEqual(before.st_ino, after.st_ino)
        self.assertEqual(stat.S_IMODE(after.st_mode), 0o600)
        with sqlite3.connect(self.outbox) as connection:
            self.assertEqual(
                connection.execute("PRAGMA user_version").fetchone()[0],
                OUTBOX_SCHEMA_VERSION,
            )
            indexes = {
                row[1]
                for row in connection.execute(
                    "PRAGMA index_list(submissions)"
                ).fetchall()
            }
        self.assertIn("idx_submissions_delivery", indexes)

    def test_create_is_idempotent_and_payload_is_validated(self) -> None:
        first, first_duplicate = self.create_fictional(
            idempotency_key="fictional-stable-key"
        )
        second, second_duplicate = self.create_fictional(
            suffix="DIFFERENT",
            idempotency_key="fictional-stable-key",
        )
        self.assertFalse(first_duplicate)
        self.assertTrue(second_duplicate)
        self.assertEqual(first.submission_uuid, second.submission_uuid)
        self.assertEqual(outbox_status(self.outbox)["queued"], 1)
        for field, value, reason in (
            ("title", "", "title_required"),
            ("title", "x" * 201, "title_too_long"),
            ("content", "", "content_required"),
            ("content", "x" * 50_001, "content_too_long"),
        ):
            arguments = {"title": "虚构", "content": "fictional"}
            arguments[field] = value
            with self.subTest(field=field, reason=reason):
                with self.assertRaisesRegex(
                    GaleSubmissionOutboxError,
                    reason,
                ):
                    create_submission(self.outbox, **arguments)

    def test_unacknowledged_is_redelivered_and_ack_is_idempotent(self) -> None:
        submission, _duplicate = self.create_fictional()
        first = export_unacknowledged(self.outbox)
        second = export_unacknowledged(self.outbox)
        self.assertEqual(
            [item.submission_uuid for item in first],
            [submission.submission_uuid],
        )
        self.assertEqual(
            [item.submission_uuid for item in second],
            [submission.submission_uuid],
        )
        acknowledged, duplicate = acknowledge_submission(
            self.outbox,
            submission_uuid=submission.submission_uuid,
            local_receipt_id="42",
        )
        repeated, repeated_duplicate = acknowledge_submission(
            self.outbox,
            submission_uuid=submission.submission_uuid,
            local_receipt_id="42",
        )
        self.assertFalse(duplicate)
        self.assertTrue(repeated_duplicate)
        self.assertEqual(acknowledged.submission_uuid, repeated.submission_uuid)
        self.assertEqual(export_unacknowledged(self.outbox), [])
        with self.assertRaisesRegex(
            GaleSubmissionOutboxError,
            "ack_receipt_conflict",
        ):
            acknowledge_submission(
                self.outbox,
                submission_uuid=submission.submission_uuid,
                local_receipt_id="43",
            )

    def test_outbox_lock_fails_without_dropping_submission(self) -> None:
        locking = sqlite3.connect(self.outbox, timeout=0)
        locking.execute("BEGIN IMMEDIATE")
        try:
            with self.assertRaisesRegex(
                GaleSubmissionOutboxError,
                "outbox_busy",
            ):
                self.create_fictional()
        finally:
            locking.rollback()
            locking.close()
        self.assertEqual(outbox_status(self.outbox)["queued"], 0)
        self.create_fictional()
        self.assertEqual(outbox_status(self.outbox)["queued"], 1)

    def test_outbox_is_separate_from_mirror_and_does_not_touch_it(self) -> None:
        mirror = self.root / "mirror" / "current" / "gale-mirror.db"
        mirror.parent.mkdir(parents=True)
        mirror.write_bytes(b"FICTIONAL_MIRROR_SENTINEL")
        before = (
            mirror.stat().st_ino,
            mirror.stat().st_size,
            mirror.stat().st_mtime_ns,
        )
        self.create_fictional()
        export_unacknowledged(self.outbox)
        after = (
            mirror.stat().st_ino,
            mirror.stat().st_size,
            mirror.stat().st_mtime_ns,
        )
        self.assertEqual(before, after)
        self.assertNotEqual(self.outbox, mirror)


if __name__ == "__main__":
    unittest.main()
