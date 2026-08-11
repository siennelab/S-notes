from __future__ import annotations

import contextlib
import hashlib
import io
import shutil
import subprocess
import tempfile
import unittest
import uuid
from pathlib import Path
from unittest.mock import patch

from src.snotes.domain.gale import GaleError
from src.snotes.transport.gale.remote import (
    MAX_TRANSPORT_BYTES,
    SystemOracleOutboxTransport,
    parse_export_payload,
)
from src.services.gale_submission_outbox import (
    GALE_PRODUCTION_ACTOR,
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

    def test_remote_output_limit_rejects_before_json_parse(self) -> None:
        with self.assertRaisesRegex(
            GaleError,
            "remote_payload_too_large",
        ):
            parse_export_payload(b"x" * (MAX_TRANSPORT_BYTES + 1))

    def test_ack_receipt_id_blocks_remote_shell_metacharacters(self) -> None:
        transport = SystemOracleOutboxTransport()
        with patch(
            "src.snotes.transport.gale.remote._run"
        ) as run_command:
            with self.assertRaisesRegex(
                GaleError,
                "local_receipt_id_invalid",
            ):
                transport.acknowledge(
                    str(uuid.uuid4()),
                    "1;touch /tmp/should-never-run",
                )
        run_command.assert_not_called()

    def test_transport_selects_only_fixed_staging_or_production_helper(self) -> None:
        for mode, expected_helper in (
            ("staging", "/usr/local/sbin/snotes-gale-r1b-outbox"),
            (
                "production",
                "/usr/local/sbin/snotes-gale-r1b-production-outbox",
            ),
        ):
            transport = SystemOracleOutboxTransport(mode=mode)
            with patch(
                "src.snotes.transport.gale.remote._run"
            ) as run_command:
                run_command.return_value = subprocess.CompletedProcess(
                    [],
                    0,
                    stdout=b'{"queued":0,"delivered":0,"acknowledged":0}',
                    stderr=b"",
                )
                transport.status()
            command = run_command.call_args.args[0]
            self.assertIn(expected_helper, command)
            self.assertNotIn("sh", command)
            self.assertNotIn("-c", command)
        with self.assertRaisesRegex(
            GaleError,
            "outbox_mode_invalid",
        ):
            SystemOracleOutboxTransport(mode="arbitrary")

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

    def test_ack_cli_positional_arguments_succeed_without_leaking_payload(
        self,
    ) -> None:
        secret_markers = (
            "FICTIONAL_ACK_TITLE_MUST_NOT_LEAK",
            "FICTIONAL_ACK_BODY_MUST_NOT_LEAK",
            "FICTIONAL_ACK_KEY_MUST_NOT_LEAK",
        )
        submission, _duplicate = create_submission(
            self.outbox,
            title=secret_markers[0],
            content=secret_markers[1],
            suggested_directory="fictional/private/path",
            suggested_tags=["fictional-private-tag"],
            idempotency_key=secret_markers[2],
        )
        export_unacknowledged(self.outbox)
        success_output = io.StringIO()
        with contextlib.redirect_stdout(success_output):
            self.assertEqual(
                outbox_main(
                    [
                        "--database",
                        str(self.outbox),
                        "ack",
                        submission.submission_uuid,
                        "1",
                    ]
                ),
                0,
            )
        self.assertEqual(outbox_status(self.outbox)["acknowledged"], 1)
        for marker in secret_markers:
            self.assertNotIn(marker, success_output.getvalue())

        failure_output = io.StringIO()
        with contextlib.redirect_stderr(failure_output):
            self.assertEqual(
                outbox_main(
                    [
                        "--database",
                        str(self.outbox),
                        "ack",
                        str(uuid.uuid4()),
                        "1",
                    ]
                ),
                2,
            )
        self.assertEqual(
            failure_output.getvalue(),
            "OUTBOX_STATUS=FAIL\nERROR_CODE=submission_not_found\n",
        )
        for marker in secret_markers:
            self.assertNotIn(marker, failure_output.getvalue())

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

        production_wrapper_path = (
            Path(__file__).resolve().parents[1]
            / "deploy"
            / "oracle"
            / "snotes-gale-r1b-production-outbox"
        )
        production_wrapper = production_wrapper_path.read_text(
            encoding="utf-8"
        )
        self.assertIn('case "${1-}" in', production_wrapper)
        self.assertIn("export|status)", production_wrapper)
        self.assertIn("ack)", production_wrapper)
        self.assertIn('""|0*|*[!0-9]*)', production_wrapper)
        self.assertNotIn('*[^0-9]*', production_wrapper)
        self.assertIn('[ "${#3}" -gt 19 ]', production_wrapper)
        self.assertIn(
            "--database /var/lib/snotes-gale-r1b-production/outbox.db",
            production_wrapper,
        )
        self.assertNotIn("staging/outbox.db", production_wrapper)
        self.assertNotIn("eval ", production_wrapper)
        self.assertNotIn("sh -c", production_wrapper)
        self.assertEqual(production_wrapper.count("--database "), 1)

        dash = shutil.which("dash")
        if dash is not None:
            guard = (
                'case "$1" in ""|0*|*[!0-9]*) exit 2;; '
                '*) exit 0;; esac'
            )
            for receipt_id in ("1", "42", "9999999999999999999"):
                with self.subTest(receipt_id=receipt_id):
                    self.assertEqual(
                        subprocess.run(
                            [dash, "-c", guard, "receipt-guard", receipt_id],
                            check=False,
                            capture_output=True,
                            text=True,
                        ).returncode,
                        0,
                    )
            for receipt_id in ("", "01", "1x", "1\n2"):
                with self.subTest(rejected_receipt_id=receipt_id):
                    self.assertEqual(
                        subprocess.run(
                            [dash, "-c", guard, "receipt-guard", receipt_id],
                            check=False,
                            capture_output=True,
                            text=True,
                        ).returncode,
                        2,
                    )

        unit = (
            Path(__file__).resolve().parents[1]
            / "deploy"
            / "oracle"
            / "snotes-gale-mcp-oidc-staging.service"
        ).read_text(encoding="utf-8")
        self.assertEqual(
            unit.count(
                "ReadWritePaths=/var/lib/snotes-gale-r1b-production"
            ),
            1,
        )
        self.assertNotIn("8879", unit)

        environment_template = (
            Path(__file__).resolve().parents[1]
            / "deploy"
            / "oracle"
            / "snotes-gale-mcp-oidc-staging.env.example"
        ).read_text(encoding="utf-8")
        self.assertIn(
            "SNOTES_GALE_R1B_OUTBOX_ROOT="
            "/var/lib/snotes-gale-r1b-production",
            environment_template,
        )
        self.assertIn(
            "SNOTES_GALE_R1B_OUTBOX_PATH="
            "/var/lib/snotes-gale-r1b-production/outbox.db",
            environment_template,
        )

    def test_production_actor_is_server_selected_and_staging_stays_default(
        self,
    ) -> None:
        production, _duplicate = create_submission(
            self.outbox,
            title="FICTIONAL_PRODUCTION_ACTOR",
            content="Fictional production actor content.",
            source_actor=GALE_PRODUCTION_ACTOR,
        )
        self.assertEqual(production.source_actor, GALE_PRODUCTION_ACTOR)
        staging, _duplicate = create_submission(
            self.outbox,
            title="FICTIONAL_STAGING_ACTOR",
            content="Fictional staging actor content.",
        )
        self.assertEqual(staging.source_actor, "gale-staging")
        with self.assertRaisesRegex(
            GaleSubmissionOutboxError,
            "source_actor_invalid",
        ):
            create_submission(
                self.outbox,
                title="FICTIONAL_INVALID_ACTOR",
                content="Fictional invalid actor content.",
                source_actor="model-controlled-actor",
            )


if __name__ == "__main__":
    unittest.main()
