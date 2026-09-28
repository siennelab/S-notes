from __future__ import annotations

import json
import re
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
from src.snotes.application.ai_receipt_inbox_service import (
    AiReceiptAcceptFields,
    AiReceiptInboxService,
)
from src.snotes.application.ai_safe_read_service import AiSafeReadService
from src.snotes.application.directory_service import DirectoryService
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
    GalePublishResult,
    GaleSubmission,
)
from src.snotes.domain.models import NoteLocation
from src.snotes.domain.markdown import markdown_to_plain_text
from src.snotes.gale_bootstrap import build_gale_control_service
from src.snotes.storage.gale_mirror import SQLiteGaleMirrorWriter
from src.snotes.storage.repositories.ai_audit import SQLiteAiAuditRepository
from src.snotes.storage.repositories.ai_receipts import SQLiteAiReceiptInboxRepository
from src.snotes.storage.repositories.directories import SQLiteDirectoriesRepository
from src.snotes.storage.repositories.notes import SQLiteNotesRepository
from src.snotes.storage.repositories.tags import SQLiteTagsRepository
from src.snotes.storage.schema import initialize_database
from src.snotes.ui.markdown_canonicalizer import canonicalize_markdown
from tests.canonicalizer_test_double import CanonicalizerTestDouble
from src.snotes.transport.gale.remote import (
    REMOTE_INCOMING,
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
        raise AssertionError("post-activate health belongs to the remote activator")

    def rollback(self, current: str, previous: str):
        if re.fullmatch(r"[0-9a-f]{64}", current) is None:
            raise AssertionError("invalid current sha256")
        if re.fullmatch(r"[0-9a-f]{64}", previous) is None:
            raise AssertionError("invalid previous sha256")
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


class FailingAutomaticMirror(FailingMirror):
    def source_fingerprint(self):
        return "f" * 64, 1


class ScheduledStatusControl:
    def __init__(self, status: str | None) -> None:
        self.status = status

    def scheduled_once(self):
        publish_result = (
            GalePublishResult(self.status, "", 0)
            if self.status is not None
            else None
        )
        return object(), publish_result

    def check_submissions_now(self):
        return object()


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
            canonicalizer=CanonicalizerTestDouble(canonicalize_markdown),
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
            AiSafeReadService(self.rules, body_renderer=markdown_to_plain_text),
            SQLiteGaleMirrorWriter(),
            self.audit,
            directories=DirectoryService(self.directories, self.note_repository),
            tags=self.tags,
            ai_metadata=self.metadata,
        )

    def _seed(
        self,
        title: str,
        visibility: str,
        targets: tuple[str, ...],
        *,
        body: str | None = None,
    ) -> int:
        location = NoteLocation("P6", title)
        note = self.notes.create_note(location)
        note = self.notes.save_note(
            note.id,
            title=title,
            content_markdown=body or title + ' body',
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

    def test_markdown_mirror_body_is_redacted_plain_text(self):
        self._seed('Markdown safety', 'redacted', ('gale',), body='FICTIONAL_**SECRET**_P6')
        path = self.root / 'markdown-mirror.db'
        self.mirror.generate(path)
        with closing(sqlite3.connect(path)) as connection:
            body = connection.execute("SELECT body FROM mirror_notes WHERE title='Markdown safety'").fetchone()[0]
        self.assertEqual(body, '[P6_SAFE_MASK]')

    def test_mirror_reuses_safe_read_and_matches_frozen_schema(self) -> None:
        path = self.root / "mirror" / "gale-mirror.db"
        result = self.mirror.generate(path)
        rows = self._mirror_rows(path)
        flattened = repr(rows)
        self.assertEqual(result.schema_version, "2")
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
            catalog_tables = {
                row[0]
                for row in connection.execute(
                    """
                    SELECT name FROM sqlite_master
                    WHERE type='table' AND name LIKE 'mirror_%'
                    """
                )
            }
        self.assertEqual(
            set(metadata),
            {
                "mirror_schema_version", "generated_at", "target_actor",
                "generator_app_version", "note_count", "generation_summary",
            },
        )
        self.assertIn("mirror_directories", catalog_tables)
        self.assertIn("mirror_tags", catalog_tables)
        self.assertIn("mirror_ai_targets", catalog_tables)

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
        self.assertNotIn("health", [call[0] for call in transport.calls])
        self.assertNotIn("rollback", [call[0] for call in transport.calls])

    def test_publish_failures_preserve_previous_without_second_rollback(self) -> None:
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
            self.assertNotIn("health", [call[0] for call in transport.calls])
            self.assertNotIn("rollback", [call[0] for call in transport.calls])

    def test_publish_fake_enforces_real_rollback_hash_contract(self) -> None:
        transport = FakePublishTransport()
        with self.assertRaisesRegex(AssertionError, "invalid previous sha256"):
            transport.rollback("a" * 64, "")
        result = transport.rollback("a" * 64, "b" * 64)
        self.assertEqual(result.status, "success")
        self.assertEqual(
            transport.calls,
            [("rollback", "a" * 64, "b" * 64)],
        )

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
        pending = self.receipts.list_pending_receipts()
        self.assertEqual(len(pending), 1)
        self.assertEqual((pending[0].main_category, pending[0].sub_category), ("临时", "Gale 投稿"))
        self.assertFalse(hasattr(pending[0], "sub_sub_category"))
        self.assertEqual(len(transport.acks), 2)
        self.assertEqual(len(self.notes.list_active_notes()), 6)

    def test_pull_prefills_metadata_and_accept_uses_s_final_fields(self) -> None:
        base = self._submission()
        submission = GaleSubmission(
            base.submission_uuid,
            base.source_actor,
            base.external_submission_id,
            base.idempotency_key,
            base.title,
            base.content,
            base.suggested_directory,
            base.suggested_tags,
            base.created_at,
            base.delivery_state,
            base.delivered_at,
            "full",
            ("gale",),
            "Gale Proposal",
            "Draft",
            ("gale-proposed",),
        )
        pull = GalePullService(
            FakeOutboxTransport((submission,)),
            self.receipts,
            self.audit,
        )
        pull.pull_once()
        pending = self.receipts.list_pending_receipts()[0]
        self.assertEqual(pending.ai_visibility, "full")
        self.assertEqual(pending.ai_targets, "gale")
        self.assertEqual(
            (pending.main_category, pending.sub_category, pending.tags),
            ("Gale Proposal", "Draft", "gale-proposed"),
        )

        accepted = self.receipts.accept_receipt(
            pending.id,
            AiReceiptAcceptFields(
                title="S final Gale note",
                body="S final body",
                main_category="S Final",
                sub_category="Chosen",
                tags="s-final",
                visibility="redacted",
                target_ids=("ke-den",),
            ),
        )
        note = accepted.note
        self.assertEqual((note.main_category, note.sub_category), ("S Final", "Chosen"))
        self.assertEqual(self.tags.get_note_tags(note.id), ["s-final"])
        metadata = self.metadata.get_note_metadata(note.id)
        self.assertEqual(metadata.visibility, "redacted")
        self.assertEqual(metadata.target_ids, ("ke-den",))

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

    def test_production_parser_skips_staging_rows_without_importing_them(self) -> None:
        submission = self._submission()
        staging_uuid = str(uuid.UUID("00000000-0000-4000-8000-000000000607"))
        raw = json.dumps(
            {
                "schema_version": 2,
                "submissions": [
                    {
                        "submission_uuid": staging_uuid,
                        "source_actor": GALE_STAGING_SOURCE_ACTOR,
                        "external_submission_id": staging_uuid,
                        "idempotency_key": "fictional-staging-607",
                        "title": "Fictional staging row",
                        "content": "Fictional staging body",
                        "suggested_directory": "临时/Gale 投稿",
                        "suggested_tags": ["staging"],
                        "created_at": "2026-08-10T00:00:00Z",
                        "delivery_state": "delivered",
                        "delivered_at": "2026-08-10T00:01:00Z",
                    },
                    {
                        "submission_uuid": submission.submission_uuid,
                        "source_actor": "gale",
                        "external_submission_id": submission.external_submission_id,
                        "idempotency_key": submission.idempotency_key,
                        "title": submission.title,
                        "content": submission.content,
                        "suggested_directory": submission.suggested_directory,
                        "suggested_tags": list(submission.suggested_tags),
                        "ai_visibility": "full",
                        "ai_targets": ["gale"],
                        "main_category": "待办",
                        "sub_category": "Gale 投稿",
                        "tags": ["gale", "mcp", "s-notes", "测试"],
                        "created_at": submission.created_at,
                        "delivery_state": "delivered",
                        "delivered_at": submission.delivered_at,
                    },
                ],
            },
            ensure_ascii=False,
        ).encode()

        parsed = parse_export_payload(raw)

        self.assertEqual(len(parsed), 1)
        self.assertEqual(parsed[0].source_actor, "gale")
        self.assertEqual(parsed[0].main_category, "待办")
        self.assertEqual(parsed[0].sub_category, "Gale 投稿")
        self.assertEqual(parsed[0].tags, ("gale", "mcp", "s-notes", "测试"))
        self.assertEqual(parsed[0].ai_visibility, "full")
        self.assertEqual(parsed[0].ai_targets, ("gale",))

    def test_production_parser_still_rejects_unknown_source_actor(self) -> None:
        submission = self._submission()
        raw = json.dumps(
            {
                "schema_version": 2,
                "submissions": [{
                    "submission_uuid": submission.submission_uuid,
                    "source_actor": "model-controlled-actor",
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

        with self.assertRaises(GaleError) as caught:
            parse_export_payload(raw)
        self.assertEqual(caught.exception.code, "remote_actor_invalid")

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

    def test_publish_transport_uses_only_fixed_production_incoming_directory(self) -> None:
        transport = SystemOraclePublishTransport()
        completed = CompletedProcess([], 0, stdout=b"", stderr=b"")
        with patch(
            "src.snotes.transport.gale.remote._run", return_value=completed
        ) as runner:
            transport.upload_candidate(
                self.root / "fictional.db", "a" * 64
            )
        self.assertIn(
            f"oracle-a1:{REMOTE_INCOMING}/" + "a" * 64 + ".db",
            runner.call_args.args[0],
        )
        with self.assertRaises(TypeError):
            SystemOraclePublishTransport(
                incoming_directory="/tmp/caller-supplied-incoming"
            )

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
        settings.set_text("gale/last_success_hash", "e" * 64)
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
            publish,
            GalePullService(outbox, self.receipts, self.audit),
            settings,
            clock=lambda: now[0],
        )
        self.assertFalse(control.auto_publish_enabled())
        self.assertTrue(control.auto_pull_enabled())
        with self.connections.read() as connection:
            note_columns = {
                str(row[1])
                for row in connection.execute("PRAGMA table_info(notes)")
            }
        self.assertNotIn("sub_sub_category", note_columns)
        control.set_auto_publish_enabled(True)
        self.assertEqual(run_one_shot(control, scheduled=True), 0)
        self.assertTrue(control.last_completed_pull_at())
        self.assertNotIn("upload", [call[0] for call in publish_transport.calls])
        now[0] += 15 * 60
        self.assertEqual(run_one_shot(control, scheduled=True), 0)
        self.assertIn("upload", [call[0] for call in publish_transport.calls])
        self.assertTrue(control.last_successful_publish_at())
        mirror_hash = settings.get_text(
            GalePublishService.LAST_SUCCESSFUL_MIRROR_HASH_KEY
        )
        source_fingerprint = settings.get_text(
            GalePublishService.LAST_SUCCESSFUL_SOURCE_FINGERPRINT_KEY
        )
        self.assertTrue(mirror_hash)
        self.assertTrue(source_fingerprint)
        self.assertNotEqual(mirror_hash, source_fingerprint)
        self.assertEqual(
            settings.get_text(GalePublishService.LEGACY_LAST_SUCCESS_HASH_KEY),
            mirror_hash,
        )
        upload_count = len([
            call for call in publish_transport.calls if call[0] == "upload"
        ])
        self.assertEqual(run_one_shot(control, scheduled=True), 0)
        self.assertEqual(
            len([
                call for call in publish_transport.calls if call[0] == "upload"
            ]),
            upload_count,
        )
        latest_publish = self.audit.list_call_logs("gale_publish")[0]
        self.assertEqual(latest_publish.status, "duplicate")
        self.assertTrue(latest_publish.is_duplicate)
        plist = desired_launch_agent(self.root / "bin" / "snotes-gale-production-pull")
        self.assertTrue(plist["RunAtLoad"])
        self.assertEqual(plist["StartInterval"], LAUNCH_AGENT_INTERVAL_SECONDS)
        self.assertEqual(plist["ProgramArguments"][-1], "--scheduled")
        destination = self.root / "LaunchAgents" / "com.snotes.gale-production-pull.plist"
        write_launch_agent_template(
            destination, self.root / "bin" / "snotes-gale-production-pull"
        )
        self.assertTrue(destination.is_file())

    def test_scheduled_exit_status_distinguishes_publish_failure(self) -> None:
        for status in (None, "success", "duplicate", "deferred"):
            self.assertEqual(
                run_one_shot(ScheduledStatusControl(status), scheduled=True),
                0,
            )
        self.assertNotEqual(
            run_one_shot(ScheduledStatusControl("failed"), scheduled=True),
            0,
        )

    def test_pull_success_publish_failure_returns_nonzero(self) -> None:
        settings = JsonGaleRuntimeSettings(
            self.root / "config" / "failed-runtime.json"
        )
        now = [1_000.0]
        publish = GalePublishService(
            FailingAutomaticMirror(),
            FakePublishTransport(),
            self.audit,
            mirror_path=self.root / "failed" / "gale-mirror.db",
            settings=settings,
            clock=lambda: now[0],
        )
        control = GaleControlService(
            publish,
            GalePullService(FakeOutboxTransport(()), self.receipts, self.audit),
            settings,
            clock=lambda: now[0],
        )
        control.set_auto_publish_enabled(True)
        self.assertEqual(run_one_shot(control, scheduled=True), 0)
        now[0] += 15 * 60
        self.assertNotEqual(run_one_shot(control, scheduled=True), 0)
        self.assertTrue(control.last_completed_pull_at())
        latest_publish = self.audit.list_call_logs("gale_publish")[0]
        latest_pull = self.audit.list_call_logs("gale_pull")[0]
        self.assertEqual(latest_pull.status, "success")
        self.assertEqual(latest_publish.status, "failed")
        self.assertEqual(
            latest_publish.failure_reason, "local_validation_failed"
        )

    def test_continuous_changes_keep_thirty_minute_publish_bound(self) -> None:
        settings = JsonGaleRuntimeSettings(
            self.root / "config" / "continuous-runtime.json"
        )
        now = [1_000.0]
        service = GalePublishService(
            self.mirror,
            FakePublishTransport(),
            self.audit,
            mirror_path=self.root / "continuous" / "gale-mirror.db",
            settings=settings,
            clock=lambda: now[0],
        )
        self.assertFalse(service._automatic_due("1" * 64))
        for digest in ("2" * 64, "3" * 64):
            now[0] += 10 * 60
            self.assertFalse(service._automatic_due(digest))
        now[0] += 10 * 60
        self.assertTrue(service._automatic_due("4" * 64))
        self.assertEqual(
            settings.get_text("gale/pending_first_at"), "1000.0"
        )

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
