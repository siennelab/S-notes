from __future__ import annotations

import json
import sqlite3
import tempfile
import unittest
import uuid
from contextlib import closing
from subprocess import CompletedProcess
from unittest.mock import patch
from pathlib import Path

from src.snotes.application.ai_access_metadata_service import AiAccessMetadataService
from src.snotes.application.ai_audit_service import AiAuditService
from src.snotes.application.ai_receipt_inbox_service import AiReceiptInboxService
from src.snotes.application.ai_safe_read_service import AiSafeReadService
from src.snotes.application.gale_service import (
    GaleControlService,
    GaleMirrorService,
    GalePublishService,
    GalePullService,
)
from src.snotes.application.note_service import NoteService
from src.snotes.application.tag_service import TagService
from src.snotes.background.gale import (
    LAUNCH_AGENT_INTERVAL_SECONDS,
    desired_launch_agent,
    run_one_shot,
    write_launch_agent_template,
)
from src.snotes.domain.gale import (
    GALE_STAGING_SOURCE_ACTOR,
    GaleActivationResult,
    GaleError,
    GaleSubmission,
)
from src.snotes.domain.models import NoteLocation
from src.snotes.exporting.ordinary import html_to_text
from src.snotes.gale_bootstrap import build_gale_control_service
from src.snotes.storage.gale_mirror import SQLiteGaleMirrorWriter
from src.snotes.storage.repositories.ai_audit import SQLiteAiAuditRepository
from src.snotes.storage.repositories.ai_receipts import SQLiteAiReceiptInboxRepository
from src.snotes.storage.repositories.directories import SQLiteDirectoriesRepository
from src.snotes.storage.repositories.notes import SQLiteNotesRepository
from src.snotes.storage.repositories.tags import SQLiteTagsRepository
from src.snotes.storage.schema import initialize_database
from src.snotes.transport.gale.remote import (
    SystemOracleOutboxTransport,
    SystemOraclePublishTransport,
    parse_export_payload,
)
from src.snotes.transport.gale.settings import JsonGaleRuntimeSettings


class FakePublishTransport:
    def __init__(self) -> None:
        self.calls: list[tuple] = []
        self.upload_error: str | None = None
        self.activation = GaleActivationResult("success", "none")
        self.healthy = True
        self.rollback_result = GaleActivationResult("success", "operator_rollback")

    def upload_candidate(self, path: Path, sha256: str) -> None:
        self.calls.append(("upload", path.name, sha256))
        if self.upload_error:
            raise GaleError(self.upload_error)

    def activate_candidate(self, sha256: str, note_count: int):
        self.calls.append(("activate", sha256, note_count))
        return self.activation

    def health(self, sha256: str) -> bool:
        self.calls.append(("health", sha256))
        return self.healthy

    def rollback(self, current: str, previous: str):
        self.calls.append(("rollback", current, previous))
        return self.rollback_result


class FakeOutboxTransport:
    def __init__(self, submissions=()) -> None:
        self.submissions = tuple(submissions)
        self.acks: list[tuple[str, str]] = []
        self.fail_ack_count = 0

    def export_unacknowledged(self):
        return self.submissions

    def acknowledge(self, submission_uuid: str, local_receipt_id: str) -> None:
        if self.fail_ack_count:
            self.fail_ack_count -= 1
            raise RuntimeError("fictional ack outage")
        self.acks.append((submission_uuid, local_receipt_id))

    def status(self):
        return {"queued": 0, "delivered": len(self.submissions), "acknowledged": 0}


class RaisingReceipts:
    def import_gale_submission(self, _submission):
        raise RuntimeError("fictional local transaction failure")


class FailingMirror:
    def generate(self, _path):
        raise GaleError("local_validation_failed")


class Package6GaleCodeGateTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory(prefix="snotes-r2-p6-")
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        config = self.root / "config"
        config.mkdir()
        self.rules = config / "local_redaction_rules.json"
        self.rules.write_text(
            json.dumps({"FICTIONAL_SECRET_P6": "[P6_SAFE_MASK]"}),
            encoding="utf-8",
        )
        self.connections = initialize_database(self.root / "s_notes.db")
        self.directories = SQLiteDirectoriesRepository(self.connections)
        self.note_repository = SQLiteNotesRepository(
            self.connections, self.directories
        )
        self.tag_repository = SQLiteTagsRepository(self.connections)
        self.notes = NoteService(self.note_repository)
        self.tags = TagService(self.tag_repository)
        self.metadata = AiAccessMetadataService(self.note_repository)
        self.audit = AiAuditService(SQLiteAiAuditRepository(self.connections))
        self.receipts = AiReceiptInboxService(
            SQLiteAiReceiptInboxRepository(
                self.connections, self.directories, self.tag_repository
            ),
            notes=self.notes,
            tags=self.tags,
            ai_metadata=self.metadata,
        )
        self._seed("common", "full", ("common",))
        self._seed("gale", "full", ("gale",))
        self._seed("clawbot", "full", ("clawbot",))
        self._seed("ke", "full", ("ke-den",))
        self._seed("hidden", "hidden", ("gale",))
        self._seed(
            "redacted FICTIONAL_SECRET_P6", "redacted", ("gale",),
            body="FICTIONAL_SECRET_P6 must never escape",
        )
        self.mirror = GaleMirrorService(
            self.note_repository,
            AiSafeReadService(self.rules, body_renderer=html_to_text),
            SQLiteGaleMirrorWriter(),
            self.audit,
        )

    def _seed(
        self,
        title: str,
        visibility: str,
        targets: tuple[str, ...],
        *,
        body: str | None = None,
    ) -> int:
        location = NoteLocation("P6", title, "无")
        note = self.notes.create_note(location)
        note = self.notes.save_note(
            note.id,
            title=title,
            content_html=f"<p>{body or title + ' body'}</p>",
            location=location,
            expected_revision=note.revision,
        )
        self.tags.set_note_tags(note.id, (f"tag-{title}",))
        self.metadata.set_note_visibility(note.id, visibility)
        self.metadata.set_note_targets(note.id, targets)
        return note.id

    def _mirror_rows(self, path: Path):
        with closing(sqlite3.connect(path)) as connection:
            return connection.execute(
                "SELECT title, body, visibility FROM mirror_notes ORDER BY source_note_id"
            ).fetchall()

    def test_mirror_reuses_safe_read_and_matches_frozen_schema(self) -> None:
        path = self.root / "mirror" / "gale-mirror.db"
        result = self.mirror.generate(path)
        rows = self._mirror_rows(path)
        flattened = repr(rows)
        self.assertEqual(result.schema_version, "1")
        self.assertEqual(result.target_actor, "gale")
        self.assertEqual(result.file_mode, "0600")
        self.assertEqual(result.note_count, 3)
        self.assertIn("common", flattened)
        self.assertIn("gale", flattened)
        self.assertIn("[P6_SAFE_MASK]", flattened)
        self.assertNotIn("FICTIONAL_SECRET_P6", flattened)
        self.assertNotIn("clawbot", flattened)
        self.assertNotIn("hidden", flattened)
        verification = self.mirror.verify(path, expected_note_count=3)
        self.assertEqual(verification.fts_count, 3)
        with closing(sqlite3.connect(path)) as connection:
            metadata = dict(connection.execute(
                "SELECT meta_key, meta_value FROM mirror_metadata"
            ))
        self.assertEqual(
            set(metadata),
            {
                "mirror_schema_version", "generated_at", "target_actor",
                "generator_app_version", "note_count", "generation_summary",
            },
        )

    def test_redaction_unavailable_skips_only_redacted(self) -> None:
        self.rules.unlink()
        path = self.root / "unavailable" / "gale-mirror.db"
        result = self.mirror.generate(path)
        flattened = repr(self._mirror_rows(path))
        self.assertEqual(result.note_count, 2)
        self.assertIn("common", flattened)
        self.assertIn("gale", flattened)
        self.assertNotIn("redacted", flattened)

    def test_manual_and_automatic_publish_share_safe_chain(self) -> None:
        transport = FakePublishTransport()
        service = GalePublishService(
            self.mirror, transport, self.audit,
            mirror_path=self.root / "publish" / "gale-mirror.db",
        )
        manual = service.publish_manual()
        automatic = service.publish_automatic()
        self.assertEqual((manual.status, automatic.status), ("success", "success"))
        self.assertEqual([call[0] for call in transport.calls].count("upload"), 2)
        self.assertEqual([call[0] for call in transport.calls].count("activate"), 2)

    def test_publish_failures_preserve_previous_and_rollback_health(self) -> None:
        old = self.root / "local-failure" / "gale-mirror.db"
        old.parent.mkdir()
        old.write_bytes(b"FICTIONAL_OLD_CURRENT")
        transport = FakePublishTransport()
        result = GalePublishService(
            FailingMirror(), transport, self.audit, mirror_path=old
        ).publish_manual()
        self.assertEqual(result.reason, "local_validation_failed")
        self.assertEqual(old.read_bytes(), b"FICTIONAL_OLD_CURRENT")
        self.assertEqual(transport.calls, [])
        for error in ("upload_failed",):
            transport = FakePublishTransport()
            transport.upload_error = error
            result = GalePublishService(
                self.mirror, transport, self.audit,
                mirror_path=self.root / error / "gale-mirror.db",
            ).publish_manual()
            self.assertEqual(result.status, "failed")
            self.assertTrue(result.previous_preserved)
            self.assertNotIn("activate", [call[0] for call in transport.calls])
        for reason in ("remote_validation_failed", "activation_failed_rolled_back"):
            transport = FakePublishTransport()
            transport.activation = GaleActivationResult(
                "failed", reason, rolled_back=reason.endswith("rolled_back")
            )
            result = GalePublishService(
                self.mirror, transport, self.audit,
                mirror_path=self.root / reason / "gale-mirror.db",
            ).publish_manual()
            self.assertEqual(result.reason, reason)
            self.assertTrue(result.previous_preserved)
        transport = FakePublishTransport()
        transport.healthy = False
        result = GalePublishService(
            self.mirror, transport, self.audit,
            mirror_path=self.root / "health" / "gale-mirror.db",
        ).publish_manual()
        self.assertEqual(result.reason, "health_failed")
        self.assertTrue(result.rolled_back)
        self.assertIn("rollback", [call[0] for call in transport.calls])

    def test_frozen_oracle_rollback_command_protocol(self) -> None:
        current = "a" * 64
        previous = "b" * 64
        completed = CompletedProcess([], 0, stdout=b"", stderr=b"")
        with patch(
            "src.snotes.transport.gale.remote._run", return_value=completed
        ) as runner:
            result = SystemOraclePublishTransport().rollback(current, previous)
        command = runner.call_args.args[0]
        self.assertEqual(result.status, "success")
        self.assertIn("rollback", command)
        self.assertEqual(command[-4:], [
            "--expected-current-sha256", current,
            "--expected-previous-sha256", previous,
        ])

    def test_pull_is_idempotent_ack_after_local_success_and_never_formal(self) -> None:
        submission = self._submission()
        transport = FakeOutboxTransport((submission,))
        pull = GalePullService(transport, self.receipts, self.audit)
        first = pull.pull_once()
        second = pull.pull_once()
        self.assertEqual((first.imported_count, first.duplicate_count), (1, 0))
        self.assertEqual((second.imported_count, second.duplicate_count), (0, 1))
        self.assertEqual(len(self.receipts.list_pending_receipts()), 1)
        self.assertEqual(len(transport.acks), 2)
        self.assertEqual(len(self.notes.list_active_notes()), 6)

    def test_local_failure_has_no_ack_and_ack_retry_has_no_duplicate(self) -> None:
        submission = self._submission()
        transport = FakeOutboxTransport((submission,))
        with self.assertRaises(RuntimeError):
            GalePullService(transport, RaisingReceipts(), self.audit).pull_once()
        self.assertEqual(transport.acks, [])
        transport.fail_ack_count = 1
        pull = GalePullService(transport, self.receipts, self.audit)
        with self.assertRaises(GaleError) as caught:
            pull.pull_once()
        self.assertEqual(caught.exception.code, "ack_failed")
        retry = pull.pull_once()
        self.assertEqual((retry.imported_count, retry.duplicate_count), (0, 1))
        self.assertEqual(len(self.receipts.list_pending_receipts()), 1)

    def test_frozen_outbox_payload_parser(self) -> None:
        submission = self._submission()
        raw = json.dumps(
            {
                "schema_version": 1,
                "submissions": [{
                    "submission_uuid": submission.submission_uuid,
                    "source_actor": "gale",
                    "external_submission_id": submission.external_submission_id,
                    "idempotency_key": submission.idempotency_key,
                    "title": submission.title,
                    "content": submission.content,
                    "suggested_directory": submission.suggested_directory,
                    "suggested_tags": list(submission.suggested_tags),
                    "created_at": submission.created_at,
                    "delivery_state": "delivered",
                    "delivered_at": submission.delivered_at,
                }],
            }
        ).encode()
        self.assertEqual(parse_export_payload(raw), (submission,))

    def test_frozen_production_outbox_command_and_ack_protocol(self) -> None:
        submission = self._submission()
        raw = json.dumps(
            {
                "schema_version": 1,
                "submissions": [{
                    "submission_uuid": submission.submission_uuid,
                    "source_actor": "gale",
                    "external_submission_id": submission.external_submission_id,
                    "idempotency_key": submission.idempotency_key,
                    "title": submission.title,
                    "content": submission.content,
                    "suggested_directory": submission.suggested_directory,
                    "suggested_tags": list(submission.suggested_tags),
                    "created_at": submission.created_at,
                    "delivery_state": "delivered",
                    "delivered_at": submission.delivered_at,
                }],
            }
        ).encode()
        completed = CompletedProcess([], 0, stdout=raw, stderr=b"")
        transport = SystemOracleOutboxTransport()
        with patch(
            "src.snotes.transport.gale.remote._run", return_value=completed
        ) as runner:
            self.assertEqual(transport.export_unacknowledged(), (submission,))
            export_command = runner.call_args.args[0]
            transport.acknowledge(submission.submission_uuid, "41")
            ack_command = runner.call_args.args[0]
        self.assertIn(
            "/usr/local/sbin/snotes-gale-r1b-production-outbox",
            export_command,
        )
        self.assertEqual(export_command[-1], "export")
        self.assertEqual(
            ack_command[-3:], ["ack", submission.submission_uuid, "41"]
        )

    def test_staging_outbox_uses_frozen_wrapper_and_source_actor(self) -> None:
        submission = self._submission()
        payload = {
            "schema_version": 1,
            "submissions": [{
                "submission_uuid": submission.submission_uuid,
                "source_actor": GALE_STAGING_SOURCE_ACTOR,
                "external_submission_id": submission.external_submission_id,
                "idempotency_key": submission.idempotency_key,
                "title": submission.title,
                "content": submission.content,
                "suggested_directory": submission.suggested_directory,
                "suggested_tags": list(submission.suggested_tags),
                "created_at": submission.created_at,
                "delivery_state": "delivered",
                "delivered_at": submission.delivered_at,
            }],
        }
        completed = CompletedProcess(
            [], 0, stdout=json.dumps(payload).encode(), stderr=b""
        )
        transport = SystemOracleOutboxTransport(mode="staging")
        with patch(
            "src.snotes.transport.gale.remote._run", return_value=completed
        ) as runner:
            values = transport.export_unacknowledged()
        self.assertEqual(values[0].source_actor, GALE_STAGING_SOURCE_ACTOR)
        self.assertIn(
            "/usr/local/sbin/snotes-gale-r1b-outbox",
            runner.call_args.args[0],
        )

    def test_publish_transport_accepts_only_named_gateb_isolated_directory(self) -> None:
        transport = SystemOraclePublishTransport(
            incoming_directory="/tmp/snotes-p6-gateb-fictional"
        )
        completed = CompletedProcess([], 0, stdout=b"", stderr=b"")
        with patch(
            "src.snotes.transport.gale.remote._run", return_value=completed
        ) as runner:
            transport.upload_candidate(
                self.root / "fictional.db", "a" * 64
            )
        self.assertIn(
            "oracle-a1:/tmp/snotes-p6-gateb-fictional/" + "a" * 64 + ".db",
            runner.call_args.args[0],
        )
        with self.assertRaises(GaleError):
            SystemOraclePublishTransport(incoming_directory="/tmp/arbitrary")

    def test_staging_pull_imports_through_existing_receipt_owner(self) -> None:
        submission = self._submission()
        staging_submission = GaleSubmission(
            submission.submission_uuid,
            GALE_STAGING_SOURCE_ACTOR,
            submission.external_submission_id,
            submission.idempotency_key,
            submission.title,
            submission.content,
            submission.suggested_directory,
            submission.suggested_tags,
            submission.created_at,
            submission.delivery_state,
            submission.delivered_at,
        )
        transport = FakeOutboxTransport((staging_submission,))
        result = GalePullService(
            transport,
            self.receipts,
            self.audit,
            expected_source_actor=GALE_STAGING_SOURCE_ACTOR,
        ).pull_once()
        self.assertEqual(result.imported_count, 1)
        self.assertEqual(len(self.receipts.list_pending_receipts()), 1)

    def test_background_defaults_and_scheduled_invocation(self) -> None:
        settings = JsonGaleRuntimeSettings(self.root / "config" / "runtime.json")
        now = [1_000.0]
        publish_transport = FakePublishTransport()
        outbox = FakeOutboxTransport(())
        publish = GalePublishService(
            self.mirror, publish_transport, self.audit,
            mirror_path=self.root / "scheduled" / "gale-mirror.db",
            settings=settings,
            clock=lambda: now[0],
        )
        control = GaleControlService(
            publish, GalePullService(outbox, self.receipts, self.audit), settings
        )
        self.assertFalse(control.auto_publish_enabled())
        self.assertTrue(control.auto_pull_enabled())
        control.set_auto_publish_enabled(True)
        self.assertEqual(run_one_shot(control, scheduled=True), 0)
        self.assertNotIn("upload", [call[0] for call in publish_transport.calls])
        now[0] += 15 * 60
        self.assertEqual(run_one_shot(control, scheduled=True), 0)
        self.assertIn("upload", [call[0] for call in publish_transport.calls])
        plist = desired_launch_agent(self.root / "bin" / "snotes-gale-production-pull")
        self.assertTrue(plist["RunAtLoad"])
        self.assertEqual(plist["StartInterval"], LAUNCH_AGENT_INTERVAL_SECONDS)
        self.assertEqual(plist["ProgramArguments"][-1], "--scheduled")
        destination = self.root / "LaunchAgents" / "com.snotes.gale-production-pull.plist"
        write_launch_agent_template(
            destination, self.root / "bin" / "snotes-gale-production-pull"
        )
        self.assertTrue(destination.is_file())

    def test_headless_composition_runs_without_gui_or_live_transport(self) -> None:
        outbox = FakeOutboxTransport(())
        publish = FakePublishTransport()
        control = build_gale_control_service(
            data_root=self.root,
            publish_transport=publish,
            outbox_transport=outbox,
        )
        result = control.check_submissions_now()
        self.assertEqual(result.exported_count, 0)

    def test_audit_is_package4_metadata_only(self) -> None:
        self.mirror.generate(self.root / "audit" / "gale-mirror.db")
        logs = self.audit.list_call_logs("gale_mirror")
        self.assertEqual(len(logs), 1)
        with closing(sqlite3.connect(self.root / "s_notes.db")) as connection:
            row = connection.execute(
                "SELECT summary, failure_reason FROM ai_call_logs WHERE operation_type='gale_mirror'"
            ).fetchone()
        self.assertEqual(row[0], "")
        self.assertNotIn("FICTIONAL_SECRET_P6", repr(row))

    @staticmethod
    def _submission() -> GaleSubmission:
        value = str(uuid.UUID("00000000-0000-4000-8000-000000000606"))
        return GaleSubmission(
            value, "gale", value, "fictional-p6-idempotency-606",
            "Fictional Gale submission", "Fictional pending body",
            "临时/Gale 投稿/待确认", ("p6",),
            "2026-08-10T00:00:00Z", "delivered", "2026-08-10T00:01:00Z",
        )


if __name__ == "__main__":
    unittest.main()
