from __future__ import annotations

import json
import os
import sqlite3
import stat
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from src.database import (
    create_gale_submission,
    initialize_database,
    list_ai_receipt_inbox_items,
    list_notes,
    search_notes,
)
from src.gale_submission_pull import (
    GaleSubmissionPullError,
    parse_export_payload,
    parse_status_payload,
    pull_submissions,
    require_isolated_data_root,
)
from src.services.gale_submission_outbox import (
    OUTBOX_SCHEMA_VERSION,
    GaleSubmissionOutboxError,
    acknowledge_submission,
    create_submission,
    export_unacknowledged,
    get_submission,
    initialize_outbox,
    outbox_status,
)


class FakeOutboxTransport:
    def __init__(self, database_path: Path) -> None:
        self.database_path = database_path
        self.ack_calls: list[tuple[str, str]] = []
        self.fail_ack_once = False
        self.fail_export = False
        self.invalid_payload: bytes | None = None
        self.duplicate_download = False

    def export_unacknowledged(self) -> bytes:
        if self.fail_export:
            raise GaleSubmissionPullError("ssh_connection_failed")
        if self.invalid_payload is not None:
            return self.invalid_payload
        rows = [
            submission.transport_payload()
            for submission in export_unacknowledged(self.database_path)
        ]
        if self.duplicate_download and rows:
            rows.append(dict(rows[0]))
        return json.dumps(
            {
                "schema_version": OUTBOX_SCHEMA_VERSION,
                "submissions": rows,
            },
            ensure_ascii=False,
            separators=(",", ":"),
        ).encode("utf-8")

    def acknowledge(
        self,
        submission_uuid: str,
        local_receipt_id: str,
    ) -> None:
        self.ack_calls.append((submission_uuid, local_receipt_id))
        if self.fail_ack_once:
            self.fail_ack_once = False
            raise GaleSubmissionPullError("fictional_ack_failure")
        acknowledge_submission(
            self.database_path,
            submission_uuid=submission_uuid,
            local_receipt_id=local_receipt_id,
        )

    def status(self) -> bytes:
        return json.dumps(
            outbox_status(self.database_path),
            separators=(",", ":"),
        ).encode("ascii")


class PackageDRemoteStagingTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory(
            prefix="snotes-r1b-remote-tests-",
            dir="/private/tmp",
        )
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.outbox = self.root / "oracle" / "outbox.db"
        self.local_root = self.root / "snotes-r1b-local-inbox"
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
        )

    def local_receipts(self) -> list[dict]:
        return list_ai_receipt_inbox_items(
            status=None,
            database_path=self.local_root / "s_notes.db",
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
        before = (mirror.stat().st_ino, mirror.stat().st_size, mirror.stat().st_mtime_ns)
        self.create_fictional()
        export_unacknowledged(self.outbox)
        after = (mirror.stat().st_ino, mirror.stat().st_size, mirror.stat().st_mtime_ns)
        self.assertEqual(before, after)
        self.assertNotEqual(self.outbox, mirror)

    def test_download_before_import_can_retry_without_loss(self) -> None:
        submission, _duplicate = self.create_fictional()
        transport = FakeOutboxTransport(self.outbox)
        downloaded = parse_export_payload(transport.export_unacknowledged())
        self.assertEqual(downloaded[0]["submission_uuid"], submission.submission_uuid)
        self.assertFalse((self.local_root / "s_notes.db").exists())
        result = pull_submissions(transport, data_root=self.local_root)
        self.assertEqual(result.imported_count, 1)
        self.assertEqual(len(self.local_receipts()), 1)
        self.assertEqual(
            get_submission(self.outbox, submission.submission_uuid).delivery_state,
            "acknowledged",
        )

    def test_local_commit_then_ack_failure_retries_without_duplicate(self) -> None:
        submission, _duplicate = self.create_fictional()
        transport = FakeOutboxTransport(self.outbox)
        transport.fail_ack_once = True
        with self.assertRaisesRegex(GaleSubmissionPullError, "ack_failed"):
            pull_submissions(transport, data_root=self.local_root)
        self.assertEqual(len(self.local_receipts()), 1)
        self.assertEqual(
            get_submission(self.outbox, submission.submission_uuid).delivery_state,
            "delivered",
        )

        retry = pull_submissions(transport, data_root=self.local_root)
        self.assertEqual(retry.imported_count, 0)
        self.assertEqual(retry.duplicate_count, 1)
        self.assertEqual(len(self.local_receipts()), 1)
        self.assertEqual(
            get_submission(self.outbox, submission.submission_uuid).delivery_state,
            "acknowledged",
        )

    def test_duplicate_download_and_existing_local_external_id_are_safe(
        self,
    ) -> None:
        submission, _duplicate = self.create_fictional()
        initialize_database(self.local_root / "s_notes.db")
        create_gale_submission(
            external_submission_id=submission.external_submission_id,
            idempotency_key=submission.idempotency_key,
            title=submission.title,
            body=submission.content,
            database_path=self.local_root / "s_notes.db",
        )
        transport = FakeOutboxTransport(self.outbox)
        transport.duplicate_download = True
        result = pull_submissions(transport, data_root=self.local_root)
        self.assertEqual(result.exported_count, 2)
        self.assertEqual(result.imported_count, 0)
        self.assertEqual(result.duplicate_count, 2)
        self.assertEqual(len(self.local_receipts()), 1)
        self.assertEqual(len(transport.ack_calls), 2)

    def test_local_import_failure_never_acknowledges(self) -> None:
        submission, _duplicate = self.create_fictional()
        transport = FakeOutboxTransport(self.outbox)
        with patch(
            "src.database.create_gale_submission",
            side_effect=RuntimeError("fictional local failure"),
        ):
            with self.assertRaisesRegex(RuntimeError, "fictional local failure"):
                pull_submissions(transport, data_root=self.local_root)
        self.assertEqual(transport.ack_calls, [])
        self.assertEqual(
            get_submission(self.outbox, submission.submission_uuid).delivery_state,
            "delivered",
        )

    def test_invalid_or_failed_transport_does_not_create_local_database(
        self,
    ) -> None:
        self.create_fictional()
        for mode in ("invalid", "failed"):
            transport = FakeOutboxTransport(self.outbox)
            if mode == "invalid":
                transport.invalid_payload = b'{"truncated":'
                expected = "remote_payload_invalid"
            else:
                transport.fail_export = True
                expected = "ssh_connection_failed"
            with self.subTest(mode=mode):
                with self.assertRaisesRegex(GaleSubmissionPullError, expected):
                    pull_submissions(transport, data_root=self.local_root)
                self.assertFalse((self.local_root / "s_notes.db").exists())

    def test_real_data_root_guard_and_status_validation(self) -> None:
        with self.assertRaisesRegex(
            GaleSubmissionPullError,
            "data_root_override_required",
        ):
            require_isolated_data_root({})
        with self.assertRaisesRegex(
            GaleSubmissionPullError,
            "data_root_not_private_tmp",
        ):
            require_isolated_data_root(
                {
                    "S_NOTES_DATA_ROOT": str(
                        Path.home()
                        / "Library"
                        / "Application Support"
                        / "S-notes"
                    )
                }
            )
        with self.assertRaisesRegex(
            GaleSubmissionPullError,
            "data_root_name_unsafe",
        ):
            require_isolated_data_root(
                {"S_NOTES_DATA_ROOT": "/private/tmp/not-r1b"}
            )
        self.assertEqual(
            parse_status_payload(
                b'{"queued":1,"delivered":2,"acknowledged":3}'
            ),
            {"queued": 1, "delivered": 2, "acknowledged": 3},
        )

    def test_imported_pending_submission_stays_out_of_formal_note_views(
        self,
    ) -> None:
        self.create_fictional()
        pull_submissions(
            FakeOutboxTransport(self.outbox),
            data_root=self.local_root,
        )
        database_path = self.local_root / "s_notes.db"
        receipts = self.local_receipts()
        self.assertEqual(len(receipts), 1)
        self.assertEqual(receipts[0]["status"], "pending")
        self.assertEqual(receipts[0]["source"], "Gale")
        self.assertEqual(list_notes(database_path), [])
        self.assertEqual(
            search_notes("FICTIONAL_REMOTE_GALE_SUBMISSION", database_path),
            [],
        )


if __name__ == "__main__":
    unittest.main()
