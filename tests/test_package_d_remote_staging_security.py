from __future__ import annotations

import contextlib
import hashlib
import io
import tempfile
import unittest
import uuid
from pathlib import Path
from unittest.mock import patch

from src.gale_submission_pull import (
    MAX_TRANSPORT_BYTES,
    GaleSubmissionPullError,
    SystemSshSubmissionTransport,
    parse_export_payload,
    require_isolated_data_root,
)
from src.services.gale_submission_outbox import (
    GaleSubmissionOutboxError,
    acknowledge_submission,
    create_submission,
    export_unacknowledged,
    initialize_outbox,
    main as outbox_main,
    outbox_status,
)


class PackageDRemoteStagingSecurityTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory(
            prefix="snotes-r1b-security-",
            dir="/private/tmp",
        )
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.outbox = self.root / "oracle" / "outbox.db"
        initialize_outbox(self.outbox)

    def test_create_rejects_undeliverable_directory_and_unstable_key(
        self,
    ) -> None:
        cases = (
            (
                {"suggested_directory": "一/二/三/四"},
                "suggested_directory_invalid",
            ),
            (
                {"suggested_directory": "一//二"},
                "suggested_directory_invalid",
            ),
            (
                {"suggested_directory": "一\n二"},
                "suggested_directory_invalid",
            ),
            (
                {"idempotency_key": "contains spaces"},
                "idempotency_key_invalid",
            ),
            (
                {"idempotency_key": "../override"},
                "idempotency_key_invalid",
            ),
            ({"title": 123}, "title_invalid"),
            ({"content": 123}, "content_invalid"),
        )
        for overrides, error_code in cases:
            arguments = {
                "title": "FICTIONAL_SECURITY_SUBMISSION",
                "content": "Fictional security validation content.",
                **overrides,
            }
            with self.subTest(error_code=error_code):
                with self.assertRaisesRegex(
                    GaleSubmissionOutboxError,
                    error_code,
                ):
                    create_submission(self.outbox, **arguments)
        self.assertEqual(
            outbox_status(self.outbox),
            {"queued": 0, "delivered": 0, "acknowledged": 0},
        )

    def test_status_is_read_only_and_missing_status_does_not_create_file(
        self,
    ) -> None:
        before_digest = hashlib.sha256(self.outbox.read_bytes()).hexdigest()
        before_metadata = (
            self.outbox.stat().st_ino,
            self.outbox.stat().st_size,
            self.outbox.stat().st_mtime_ns,
        )
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            self.assertEqual(
                outbox_main(
                    ["--database", str(self.outbox), "status"]
                ),
                0,
            )
        self.assertIn('"queued":0', output.getvalue())
        self.assertEqual(
            hashlib.sha256(self.outbox.read_bytes()).hexdigest(),
            before_digest,
        )
        self.assertEqual(
            (
                self.outbox.stat().st_ino,
                self.outbox.stat().st_size,
                self.outbox.stat().st_mtime_ns,
            ),
            before_metadata,
        )

        missing = self.root / "missing" / "outbox.db"
        with contextlib.redirect_stderr(io.StringIO()):
            self.assertEqual(
                outbox_main(["--database", str(missing), "status"]),
                2,
            )
        self.assertFalse(missing.exists())

    def test_symlink_canonical_and_traversal_paths_are_rejected(self) -> None:
        formal_root = (
            Path.home() / "Library" / "Application Support" / "S-notes"
        )
        link = self.root / "snotes-r1b-formal-link"
        link.symlink_to(formal_root, target_is_directory=True)
        with self.assertRaisesRegex(
            GaleSubmissionPullError,
            "data_root_symlink",
        ):
            require_isolated_data_root(
                {"S_NOTES_DATA_ROOT": str(link)}
            )

        traversal = (
            Path("/private/tmp")
            / "snotes-r1b-hop"
            / ".."
            / ".."
            / ".."
            / "Users"
            / Path.home().name
            / "Library"
            / "Application Support"
            / "S-notes"
        )
        with self.assertRaisesRegex(
            GaleSubmissionPullError,
            "data_root_not_private_tmp",
        ):
            require_isolated_data_root(
                {"S_NOTES_DATA_ROOT": str(traversal)}
            )

        project_data = Path(__file__).resolve().parents[1] / "data"
        with self.assertRaisesRegex(
            GaleSubmissionPullError,
            "data_root_not_private_tmp",
        ):
            require_isolated_data_root(
                {"S_NOTES_DATA_ROOT": str(project_data)}
            )

    def test_remote_output_limit_rejects_before_json_parse(self) -> None:
        with self.assertRaisesRegex(
            GaleSubmissionPullError,
            "remote_payload_too_large",
        ):
            parse_export_payload(b"x" * (MAX_TRANSPORT_BYTES + 1))

    def test_ack_receipt_id_blocks_remote_shell_metacharacters(self) -> None:
        transport = SystemSshSubmissionTransport()
        with patch(
            "src.gale_submission_pull.subprocess.run"
        ) as run_command:
            with self.assertRaisesRegex(
                GaleSubmissionPullError,
                "local_receipt_id_invalid",
            ):
                transport.acknowledge(
                    str(uuid.uuid4()),
                    "1;touch /tmp/should-never-run",
                )
        run_command.assert_not_called()

    def test_ack_rejects_unknown_or_not_delivered_submission(self) -> None:
        submission, _duplicate = create_submission(
            self.outbox,
            title="FICTIONAL_ACK_STATE",
            content="Fictional ack state content.",
            idempotency_key="fictional-ack-state",
        )
        with self.assertRaisesRegex(
            GaleSubmissionOutboxError,
            "submission_not_delivered",
        ):
            acknowledge_submission(
                self.outbox,
                submission_uuid=submission.submission_uuid,
                local_receipt_id="1",
            )
        export_unacknowledged(self.outbox)
        with self.assertRaisesRegex(
            GaleSubmissionOutboxError,
            "submission_not_found",
        ):
            acknowledge_submission(
                self.outbox,
                submission_uuid=str(uuid.uuid4()),
                local_receipt_id="1",
            )
        self.assertEqual(outbox_status(self.outbox)["delivered"], 1)

    def test_wrapper_has_fixed_command_and_database_allowlist(self) -> None:
        wrapper = (
            Path(__file__).resolve().parents[1]
            / "deploy"
            / "oracle"
            / "snotes-gale-r1b-outbox"
        ).read_text(encoding="utf-8")
        self.assertIn('case "${1-}" in', wrapper)
        self.assertIn("export|status)", wrapper)
        self.assertIn("ack)", wrapper)
        self.assertIn('""|0*|*[^0-9]*)', wrapper)
        self.assertIn('[ "${#3}" -gt 19 ]', wrapper)
        self.assertIn(
            "--database /var/lib/snotes-gale-r1b-staging/outbox.db",
            wrapper,
        )
        self.assertNotIn("eval ", wrapper)
        self.assertNotIn("sh -c", wrapper)
        self.assertEqual(wrapper.count("--database "), 1)


if __name__ == "__main__":
    unittest.main()
