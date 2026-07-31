from __future__ import annotations

import contextlib
import hashlib
import io
import sqlite3
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
    require_production_data_root,
    validate_production_database,
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

        production_home = self.root.resolve() / "fictional-home"
        production_root = (
            production_home
            / "Library"
            / "Application Support"
            / "S-notes"
        )
        production_root.parent.mkdir(parents=True)
        production_root.symlink_to(formal_root, target_is_directory=True)
        with self.assertRaisesRegex(
            GaleSubmissionPullError,
            "production_data_root_symlink",
        ):
            require_production_data_root({}, home=production_home)

    def test_remote_output_limit_rejects_before_json_parse(self) -> None:
        with self.assertRaisesRegex(
            GaleSubmissionPullError,
            "remote_payload_too_large",
        ):
            parse_export_payload(b"x" * (MAX_TRANSPORT_BYTES + 1))

    def test_production_database_gate_is_read_only_and_requires_schema_two(
        self,
    ) -> None:
        production_root = self.root / "production-data"
        production_root.mkdir()
        database_path = production_root / "s_notes.db"
        with sqlite3.connect(database_path) as connection:
            connection.execute(
                "CREATE TABLE schema_meta (meta_key TEXT, meta_value TEXT)"
            )
            connection.execute(
                "INSERT INTO schema_meta VALUES ('ai_foundation_version', '1')"
            )
        before = hashlib.sha256(database_path.read_bytes()).hexdigest()
        with self.assertRaisesRegex(
            GaleSubmissionPullError,
            "production_schema_unsupported",
        ):
            validate_production_database(production_root)
        self.assertEqual(
            hashlib.sha256(database_path.read_bytes()).hexdigest(),
            before,
        )

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

    def test_transport_selects_only_fixed_staging_or_production_helper(self) -> None:
        for mode, expected_helper in (
            ("staging", "/usr/local/sbin/snotes-gale-r1b-outbox"),
            (
                "production",
                "/usr/local/sbin/snotes-gale-r1b-production-outbox",
            ),
        ):
            transport = SystemSshSubmissionTransport(mode=mode)
            with patch(
                "src.gale_submission_pull.subprocess.run"
            ) as run_command:
                run_command.return_value.returncode = 0
                run_command.return_value.stdout = b'{}'
                transport.status()
            command = run_command.call_args.args[0]
            self.assertIn(expected_helper, command)
            self.assertNotIn("sh", command)
            self.assertNotIn("-c", command)
        with self.assertRaisesRegex(
            GaleSubmissionPullError,
            "pull_mode_not_allowed",
        ):
            SystemSshSubmissionTransport(mode="arbitrary")

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
        self.assertIn('""|0*|*[^0-9]*)', production_wrapper)
        self.assertIn('[ "${#3}" -gt 19 ]', production_wrapper)
        self.assertIn(
            "--database /var/lib/snotes-gale-r1b-production/outbox.db",
            production_wrapper,
        )
        self.assertNotIn("staging/outbox.db", production_wrapper)
        self.assertNotIn("eval ", production_wrapper)
        self.assertNotIn("sh -c", production_wrapper)
        self.assertEqual(production_wrapper.count("--database "), 1)

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
