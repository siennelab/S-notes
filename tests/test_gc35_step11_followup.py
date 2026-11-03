from __future__ import annotations

import hashlib
import io
import json
import tempfile
import unittest
from pathlib import Path
from subprocess import CompletedProcess
from types import SimpleNamespace
from unittest.mock import patch

import gale_production_pull_helper

from deploy.greencloud.outbox.transport import dispatch as cloud_dispatch
from src.services.actor_mirror_publish_service import (
    ActorMirrorPublisher,
    PublishReceipt,
)
from src.services.gale_submission_outbox import (
    get_operation_status,
    initialize_outbox,
)
from src.snotes.application.ai_access_metadata_service import AiAccessMetadataService
from src.snotes.application.ai_audit_service import AiAuditService
from src.snotes.application.ai_receipt_inbox_service import (
    AiReceiptAcceptFields,
    AiReceiptInboxService,
)
from src.snotes.application.cloud_proposal_service import CloudProposalService
from src.snotes.application.ai_write_authorization import AiWriteAuthorization
from src.snotes.application.directory_service import DirectoryService
from src.snotes.application.gale_service import GreenCloudPullService
from src.snotes.application.greencloud_receipt_service import GreenCloudReceiptService
from src.snotes.application.note_service import NoteService
from src.snotes.application.tag_service import TagService
from src.snotes.gale_bootstrap import compose_gale_control
from src.snotes.domain.ai_receipts import GaleReceiptImport
from src.snotes.domain.models import NoteLocation
from src.snotes.storage.cloud_mirror_receiver import receive_publish
from src.snotes.storage.cloud_read import SQLiteCloudMirrorReader
from src.snotes.storage.repositories.ai_audit import SQLiteAiAuditRepository
from src.snotes.storage.repositories.ai_mcp_operations import SQLiteAiMcpOperationRepository
from src.snotes.storage.repositories.ai_receipts import SQLiteAiReceiptInboxRepository
from src.snotes.storage.repositories.directories import SQLiteDirectoriesRepository
from src.snotes.storage.repositories.notes import SQLiteNotesRepository
from src.snotes.storage.repositories.tags import SQLiteTagsRepository
from src.snotes.storage.schema import initialize_database
from src.snotes.transport.cloud_outbox_http import _ActorOutboxGateway
from src.snotes.transport.greencloud.outbox import SystemGreenCloudOutboxTransport
from tests.canonicalizer_test_double import CanonicalizerTestDouble


class ReceiverTransport:
    """Exercise the real Cloud receiver against private temporary actor roots."""

    def __init__(self, mirror_root: Path) -> None:
        self.mirror_root = Path(mirror_root)
        self.calls: list[str] = []
        self.fail_next_actor = ""
        for actor in ("claude-code", "clawbot", "gale", "s"):
            (self.mirror_root / actor).mkdir(parents=True, mode=0o700)

    def publish(
        self,
        *,
        actor: str,
        mirror_path: Path,
        file_sha256: str,
        content_hash: str,
    ) -> PublishReceipt:
        self.calls.append(actor)
        if self.fail_next_actor == actor:
            self.fail_next_actor = ""
            raise RuntimeError("fictional mirror receiver outage")
        payload = Path(mirror_path).read_bytes()
        self.assert_hash(payload, file_sha256)
        header = json.dumps(
            {"actor": actor, "size": len(payload), "sha256": file_sha256},
            separators=(",", ":"),
        ).encode() + b"\n"
        response = receive_publish(
            io.BytesIO(header + payload), mirror_root=self.mirror_root
        )
        if response["hash"] != content_hash:
            raise AssertionError("receiver content hash mismatch")
        return PublishReceipt(
            str(response["actor"]),
            str(response["hash"]),
            int(response["note_count"]),
        )

    @staticmethod
    def assert_hash(payload: bytes, expected: str) -> None:
        if hashlib.sha256(payload).hexdigest() != expected:
            raise AssertionError("publisher file hash mismatch")


class FakeSshOutbox:
    """Route the real Mac SSH adapter into Step 10's fixed Cloud dispatcher."""

    def __init__(self, database: Path) -> None:
        self.database = Path(database)
        self.receipt_calls = 0
        self.receipt_duplicates: list[bool] = []
        self.lose_next_receipt_reply = False

    def __call__(self, command, **kwargs):
        action = {
            "notes-outbox-export": "export",
            "notes-outbox-acknowledge": "acknowledge",
            "notes-outbox-receipt": "receipt",
        }[command[-1]]
        request = json.loads(kwargs["input"])
        result = cloud_dispatch(action, request, database=self.database)
        if action == "receipt":
            self.receipt_calls += 1
            self.receipt_duplicates.append(bool(result["duplicate"]))
            if self.lose_next_receipt_reply:
                self.lose_next_receipt_reply = False
                return CompletedProcess(command, 255, b"", b"fictional lost reply")
        return CompletedProcess(
            command,
            0,
            json.dumps(result, ensure_ascii=False).encode(),
            b"",
        )


class RecordingActorPublisher:
    def __init__(self) -> None:
        self.calls: list[str] = []
        self.result = SimpleNamespace(actor="gale", status="published")

    def publish_actor(self, actor: str):
        self.calls.append(actor)
        return self.result


class Step11FollowupIntegrationTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory(
            prefix="snotes-step11-followup-", dir="/private/tmp"
        )
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.local_database = self.root / "local" / "s_notes.db"
        self.outbox = self.root / "cloud" / "outbox.db"
        initialize_outbox(self.outbox, generalized=True)

        self.connections = initialize_database(self.local_database)
        self.directory_repository = SQLiteDirectoriesRepository(self.connections)
        self.note_repository = SQLiteNotesRepository(
            self.connections, self.directory_repository
        )
        self.tag_repository = SQLiteTagsRepository(self.connections)
        self.notes = NoteService(self.note_repository)
        self.tags = TagService(self.tag_repository)
        self.metadata = AiAccessMetadataService(self.note_repository)
        self.audit = AiAuditService(SQLiteAiAuditRepository(self.connections))
        self.receipts = AiReceiptInboxService(
            SQLiteAiReceiptInboxRepository(
                self.connections,
                self.directory_repository,
                self.tag_repository,
            ),
            notes=self.notes,
            tags=self.tags,
            ai_metadata=self.metadata,
            mcp_operations=SQLiteAiMcpOperationRepository(
                self.connections, self.tag_repository
            ),
            canonicalizer=CanonicalizerTestDouble(lambda value: value),
            authorization=AiWriteAuthorization(
                directories=DirectoryService(self.directory_repository)
            ),
        )
        blank = self.notes.create_note(NoteLocation("Step 11", "Followup"))
        self.note = self.notes.save_note(
            blank.id,
            title="Fictional shared note",
            content_markdown="Fictional base\n",
            location=blank.location,
            expected_revision=blank.revision,
        )
        self.metadata.set_note_visibility(self.note.id, "full")
        self.metadata.set_note_targets(
            self.note.id, ("claude-code", "clawbot", "gale")
        )

        rules = self.root / "local" / "config" / "local_redaction_rules.json"
        rules.parent.mkdir(parents=True, exist_ok=True)
        rules.write_text('{"FICTIONAL_SECRET":"[MASKED]"}', encoding="utf-8")
        self.receiver = ReceiverTransport(self.root / "cloud" / "mirror")
        self.publisher = ActorMirrorPublisher(
            source_database=self.local_database,
            redaction_rules=rules,
            mirror_directory=self.root / "local" / "mirrors",
            state_path=self.root / "local" / "publisher-state.json",
            lock_path=self.root / "local" / "publisher.lock",
            transport=self.receiver,
        )
        initial = self.publisher.publish()
        self.assertEqual([item.status for item in initial], ["published", "published", "published", "published"])

        self.identity = self.root / "mirror-key"
        self.identity.write_text("fictional", encoding="utf-8")
        self.identity.chmod(0o600)
        self.cloud_transport = SystemGreenCloudOutboxTransport(
            identity_file=self.identity
        )
        self.ssh = FakeSshOutbox(self.outbox)
        self.pull = GreenCloudPullService(
            self.cloud_transport, self.receipts, self.audit
        )
        self.followup = GreenCloudReceiptService(
            self.receipts, self.cloud_transport, self.publisher
        )

    def _reader(self, actor: str) -> SQLiteCloudMirrorReader:
        return SQLiteCloudMirrorReader(
            self.root / "cloud" / "mirror" / actor / "current.db"
        )

    def test_composition_reuses_publisher_for_manual_gale_publish(self) -> None:
        publisher = RecordingActorPublisher()
        control = compose_gale_control(
            self.root / "local",
            self.note_repository,
            self.receipts,
            self.audit,
            greencloud_transport=self.cloud_transport,
            actor_publisher=publisher,
        )

        followup_owner = control._greencloud_followup.__self__
        self.assertIs(followup_owner._publisher, publisher)
        self.assertIs(control.publish_greencloud_mirror_now(), publisher.result)
        self.assertEqual(publisher.calls, ["gale"])

    def _proposal(self, actor: str) -> CloudProposalService:
        return CloudProposalService(
            self._reader(actor), _ActorOutboxGateway(self.outbox, actor)
        )

    def _queue_append(self, actor: str, request_id: str, content: str = "Tail"):
        mirror_note = self._reader(actor).get(self.note.id)
        assert mirror_note is not None
        result = self._proposal(actor).append_note(
            request_id=request_id,
            note_id=self.note.id,
            content=content,
            expected_version=str(mirror_note["version"]),
        )
        self.assertEqual(result["delivery_state"], "queued")
        return result

    def _pull_once(self):
        with patch(
            "src.snotes.transport.greencloud.outbox.subprocess.run",
            side_effect=self.ssh,
        ):
            return self.pull.pull_once()

    def _flush(self):
        with patch(
            "src.snotes.transport.greencloud.outbox.subprocess.run",
            side_effect=self.ssh,
        ):
            return self.followup.flush()

    def _accept(self, receipt_id: int):
        receipt = self.receipts.get_receipt(receipt_id)
        assert receipt is not None
        return self.receipts.accept_receipt(
            receipt_id,
            AiReceiptAcceptFields(
                title=receipt.title,
                body=receipt.body,
                main_category=receipt.main_category,
                sub_category=receipt.sub_category,
                tags=receipt.tags,
                visibility=receipt.ai_visibility,
                target_ids=("claude-code", "clawbot", "gale"),
            ),
        )

    def test_accepted_refreshes_only_origin_actor_before_terminal_receipt(self) -> None:
        request_id = "accepted-immediate-refresh"
        self._queue_append("claude-code", request_id, "Accepted tail")
        pulled = self._pull_once()
        self.assertEqual((pulled.imported_count, pulled.acknowledged_count), (1, 1))
        receipt = self.receipts.list_pending_receipts()[0]
        accepted = self._accept(receipt.id)
        self.assertTrue(accepted.outcome.accepted)

        before_clawbot = self._reader("clawbot").get_markdown(self.note.id)
        followup = self._flush()
        self.assertEqual(
            (followup.sent_count, followup.mirror_published_count, followup.failed_count),
            (1, 1, 0),
        )
        claude = self._reader("claude-code").get_markdown(self.note.id)
        clawbot = self._reader("clawbot").get_markdown(self.note.id)
        self.assertIn("Accepted tail", claude["markdown"])
        self.assertEqual(clawbot, before_clawbot)
        status = self._proposal("claude-code").get_operation_status(request_id)
        self.assertEqual(status["delivery_state"], "accepted")

    def test_prepend_then_metadata_refreshes_cloud_read_without_rewriting_body(self) -> None:
        original = self.notes.get_note(self.note.id)
        before_other_actor = self._reader("clawbot").get_markdown(self.note.id)
        first = self._proposal("claude-code").prepend_note(
            request_id="extension-prepend",
            note_id=self.note.id,
            content="Fictional prefix\n\n",
            expected_version=self._reader("claude-code").get(self.note.id)["version"],
        )
        self.assertEqual(first["status"], "queued")
        self.assertEqual(self._pull_once().imported_count, 1)
        receipt = self.receipts.list_pending_receipts()[0]
        self.assertEqual(receipt.operation_type, "prepend")
        self.assertTrue(self._accept(receipt.id).outcome.accepted)
        prepended = self.notes.get_note(self.note.id)
        self.assertTrue(prepended.content_markdown.startswith("Fictional prefix"))
        self.assertEqual(prepended.content_markdown.count("Fictional prefix"), 1)
        self.assertIn(original.content_markdown, prepended.content_markdown)
        self.assertGreater(prepended.revision, original.revision)
        self.assertEqual(self._flush().mirror_published_count, 1)
        self.assertEqual(
            self._proposal("claude-code").get_operation_status("extension-prepend")["status"],
            "accepted",
        )
        mirrored = self._reader("claude-code").get_markdown(self.note.id)
        self.assertEqual(mirrored["markdown"], prepended.content_markdown)
        self.assertNotEqual(mirrored["version"], before_other_actor["version"])

        destination = self.directory_repository.create("Extension destination")
        second = self._proposal("claude-code").update_note_metadata(
            request_id="extension-metadata",
            note_id=self.note.id,
            expected_version=mirrored["version"],
            title="Fictional metadata title",
            directory_id=destination.id,
            add_tags=["extension"],
            remove_tags=["fictional"],
        )
        self.assertEqual(second["status"], "queued")
        self.assertEqual(self._pull_once().imported_count, 1)
        receipt = self.receipts.list_pending_receipts()[0]
        self.assertEqual(receipt.operation_type, "update_note_metadata")
        self.assertTrue(self._accept(receipt.id).outcome.accepted)
        updated = self.notes.get_note(self.note.id)
        self.assertEqual(updated.content_markdown, prepended.content_markdown)
        self.assertEqual(updated.title, "Fictional metadata title")
        self.assertEqual(updated.directory_id, destination.id)
        self.assertIn("extension", self.tags.get_note_tags(self.note.id))
        self.assertGreater(updated.revision, prepended.revision)
        self.assertEqual(self._flush().mirror_published_count, 1)
        status = self._proposal("claude-code").get_operation_status("extension-metadata")
        self.assertEqual(status["status"], "accepted")
        self.assertEqual(status["operation_type"], "update_note_metadata")
        refreshed = self._reader("claude-code").get_markdown(self.note.id)
        self.assertEqual(refreshed["markdown"], prepended.content_markdown)
        self.assertNotEqual(refreshed["version"], mirrored["version"])
        readable = self._reader("claude-code").get(self.note.id)
        self.assertEqual(readable["title"], updated.title)
        self.assertIn("extension", readable["tags"])
        self.assertIn("Extension destination", readable["directory"])
        self.assertEqual(self._reader("clawbot").get_markdown(self.note.id), before_other_actor)

    def test_new_operations_reject_and_conflict_return_original_status_without_publish(self) -> None:
        for operation in ("prepend", "update_note_metadata"):
            with self.subTest(operation=operation):
                def queue(request_id):
                    current = self._reader("claude-code").get(self.note.id)
                    arguments = dict(
                        request_id=request_id,
                        note_id=self.note.id,
                        expected_version=current["version"],
                    )
                    if operation == "prepend":
                        return self._proposal("claude-code").prepend_note(
                            **arguments, content="Must not prepend\n"
                        )
                    return self._proposal("claude-code").update_note_metadata(
                        **arguments, title="Must not rename", add_tags=["must-not-add"]
                    )

                rejected_id = "extension-reject-" + operation
                self.assertEqual(queue(rejected_id)["status"], "queued")
                self._pull_once()
                receipt = self.receipts.list_pending_receipts()[0]
                before_note = self.notes.get_note(self.note.id)
                self.receipts.discard_receipt(receipt.id)
                calls_before = len(self.receiver.calls)
                flushed = self._flush()
                self.assertEqual((flushed.sent_count, flushed.mirror_published_count), (1, 0))
                self.assertEqual(self.notes.get_note(self.note.id), before_note)
                self.assertEqual(len(self.receiver.calls), calls_before)
                self.assertEqual(self._proposal("claude-code").get_operation_status(rejected_id)["status"], "rejected")
                self.assertEqual(self._proposal("clawbot").get_operation_status(rejected_id)["status"], "not_found")

                conflict_id = "extension-conflict-" + operation
                self.assertEqual(queue(conflict_id)["status"], "queued")
                self._pull_once()
                receipt = self.receipts.list_pending_receipts()[0]
                current = self.notes.get_note(self.note.id)
                self.notes.save_note(
                    current.id,
                    title="Concurrent title " + operation,
                    content_markdown=current.content_markdown,
                    location=current.location,
                    expected_revision=current.revision,
                )
                before_note = self.notes.get_note(self.note.id)
                before_tags = self.tags.get_note_tags(self.note.id)
                self.assertEqual(self._accept(receipt.id).outcome.kind.value, "conflict")
                flushed = self._flush()
                self.assertEqual((flushed.sent_count, flushed.mirror_published_count), (1, 0))
                self.assertEqual(self.notes.get_note(self.note.id), before_note)
                self.assertEqual(self.tags.get_note_tags(self.note.id), before_tags)
                self.assertEqual(len(self.receiver.calls), calls_before)
                self.assertEqual(self._proposal("claude-code").get_operation_status(conflict_id)["status"], "conflict")

    def test_rejected_and_conflict_write_terminal_without_publishing(self) -> None:
        self._queue_append("claude-code", "human-rejected", "Rejected tail")
        self._pull_once()
        rejected = self.receipts.list_pending_receipts()[0]
        self.receipts.discard_receipt(rejected.id)
        calls_before = len(self.receiver.calls)
        flushed = self._flush()
        self.assertEqual((flushed.sent_count, flushed.mirror_published_count), (1, 0))
        self.assertEqual(len(self.receiver.calls), calls_before)
        self.assertEqual(
            self._proposal("claude-code").get_operation_status("human-rejected")[
                "delivery_state"
            ],
            "rejected",
        )

        self._queue_append("claude-code", "stale-conflict", "Conflict tail")
        self._pull_once()
        conflict_receipt = self.receipts.list_pending_receipts()[0]
        current = self.notes.get_note(self.note.id)
        self.notes.save_note(
            current.id,
            title=current.title,
            content_markdown="Concurrent local edit\n",
            location=current.location,
            expected_revision=current.revision,
        )
        conflict = self._accept(conflict_receipt.id)
        self.assertEqual(conflict.outcome.kind.value, "conflict")
        calls_before = len(self.receiver.calls)
        flushed = self._flush()
        self.assertEqual((flushed.sent_count, flushed.mirror_published_count), (1, 0))
        self.assertEqual(len(self.receiver.calls), calls_before)
        self.assertEqual(
            self._proposal("claude-code").get_operation_status("stale-conflict")[
                "delivery_state"
            ],
            "conflict",
        )

    def test_lost_receipt_reply_retries_idempotently_without_republish(self) -> None:
        request_id = "lost-terminal-reply"
        self._queue_append("claude-code", request_id, "Retry tail")
        self._pull_once()
        receipt = self.receipts.list_pending_receipts()[0]
        self.assertTrue(self._accept(receipt.id).outcome.accepted)
        self.ssh.lose_next_receipt_reply = True
        first = self._flush()
        self.assertEqual((first.sent_count, first.mirror_published_count, first.failed_count), (0, 1, 1))
        calls_after_publish = len(self.receiver.calls)
        self.assertEqual(
            get_operation_status(
                self.outbox,
                source_actor="claude-code",
                request_id=request_id,
            ).delivery_state,
            "accepted",
        )
        second = self._flush()
        self.assertEqual((second.sent_count, second.mirror_published_count, second.failed_count), (1, 0, 0))
        self.assertEqual(len(self.receiver.calls), calls_after_publish)
        self.assertEqual(self.ssh.receipt_duplicates, [False, True])
        self.assertEqual(self.receipts.pending_greencloud_terminal_receipts(), ())

    def test_mirror_failure_keeps_accepted_decision_pending_for_retry(self) -> None:
        request_id = "mirror-retry-before-receipt"
        self._queue_append("claude-code", request_id, "Mirror retry tail")
        self._pull_once()
        receipt = self.receipts.list_pending_receipts()[0]
        self.assertTrue(self._accept(receipt.id).outcome.accepted)
        self.receiver.fail_next_actor = "claude-code"
        first = self._flush()
        self.assertEqual((first.sent_count, first.mirror_published_count, first.failed_count), (0, 0, 1))
        self.assertEqual(
            self._proposal("claude-code").get_operation_status(request_id)[
                "delivery_state"
            ],
            "acknowledged",
        )
        pending = self.receipts.pending_greencloud_terminal_receipts()
        self.assertEqual(len(pending), 1)
        self.assertFalse(pending[0].mirror_published)
        second = self._flush()
        self.assertEqual((second.sent_count, second.mirror_published_count, second.failed_count), (1, 1, 0))
        self.assertEqual(
            self._proposal("claude-code").get_operation_status(request_id)[
                "delivery_state"
            ],
            "accepted",
        )

    def test_scheduled_helper_failure_keeps_decision_retryable_until_delivery(self) -> None:
        for auto_pull in (False, True):
            for failure in ("mirror", "receipt"):
                with self.subTest(auto_pull=auto_pull, failure=failure):
                    request_id = f"helper-retry-{auto_pull}-{failure}"
                    self._queue_append("claude-code", request_id, request_id)
                    self._pull_once()
                    receipt = self.receipts.list_pending_receipts()[0]
                    self.assertTrue(self._accept(receipt.id).outcome.accepted)
                    accepted_note = self.notes.get_note(self.note.id)
                    control = compose_gale_control(
                        self.root / "local", self.note_repository,
                        self.receipts, self.audit,
                        outbox_transport=SimpleNamespace(
                            export_unacknowledged=lambda: ()
                        ),
                        greencloud_transport=self.cloud_transport,
                        actor_publisher=self.publisher,
                    )
                    control.set_auto_pull_enabled(auto_pull)
                    if failure == "mirror":
                        self.receiver.fail_next_actor = "claude-code"
                    with patch(
                        "src.snotes.transport.greencloud.outbox.subprocess.run",
                        side_effect=self.ssh,
                    ), patch.object(
                        gale_production_pull_helper, "build_gale_control_service",
                        return_value=control,
                    ):
                        with patch.object(
                            self.cloud_transport, "write_terminal_receipt",
                            wraps=self.cloud_transport.write_terminal_receipt,
                        ) as write_receipt:
                            if failure == "receipt":
                                write_receipt.side_effect = RuntimeError(
                                    "fictional receipt outage"
                                )
                            self.assertEqual(
                                gale_production_pull_helper.main(["--scheduled"]), 2
                            )
                        pending = self.receipts.pending_greencloud_terminal_receipts()
                        self.assertEqual(len(pending), 1)
                        self.assertEqual(pending[0].receipt_id, receipt.id)
                        self.assertEqual(pending[0].mirror_published, failure == "receipt")
                        self.assertEqual(
                            self._proposal("claude-code").get_operation_status(
                                request_id
                            )["delivery_state"],
                            "acknowledged",
                        )
                        calls_before_retry = len(self.receiver.calls)
                        self.assertEqual(
                            gale_production_pull_helper.main(["--scheduled"]), 0
                        )
                    self.assertEqual(
                        self.receipts.pending_greencloud_terminal_receipts(), ()
                    )
                    self.assertEqual(
                        self._proposal("claude-code").get_operation_status(
                            request_id
                        )["delivery_state"],
                        "accepted",
                    )
                    self.assertEqual(
                        len(self.receiver.calls) - calls_before_retry,
                        1 if failure == "mirror" else 0,
                    )
                    self.assertEqual(self.notes.get_note(self.note.id), accepted_note)

    def test_same_cloud_request_id_two_actors_keep_independent_terminal_states(self) -> None:
        request_id = "same-cloud-request-two-actors"
        self._queue_append("claude-code", request_id, "Claude accepted tail")
        self._queue_append("clawbot", request_id, "Clawbot rejected tail")
        self._pull_once()
        by_actor = {receipt.actor_target_id: receipt for receipt in self.receipts.list_pending_receipts()}
        self.assertTrue(self._accept(by_actor["claude-code"].id).outcome.accepted)
        self.receipts.discard_receipt(by_actor["clawbot"].id)
        before = len(self.receiver.calls)
        result = self._flush()
        self.assertEqual((result.sent_count, result.failed_count), (2, 0))
        self.assertEqual(self.receiver.calls[before:], ["claude-code"])
        self.assertEqual(self._proposal("claude-code").get_operation_status(request_id)["delivery_state"], "accepted")
        self.assertEqual(self._proposal("clawbot").get_operation_status(request_id)["delivery_state"], "rejected")

    def test_gale_cloud_proposal_acceptance_refreshes_gale_and_returns_receipt(self) -> None:
        request_id = "fictional-greencloud-gale-accept"
        self._queue_append("gale", request_id, "FICTIONAL Gale accepted tail")
        self._pull_once()
        receipt = self.receipts.list_pending_receipts()[0]
        self.assertEqual((receipt.source, receipt.actor_target_id), ("GreenCloud", "gale"))
        self.assertTrue(self._accept(receipt.id).outcome.accepted)
        before = len(self.receiver.calls)
        result = self._flush()
        self.assertEqual((result.sent_count, result.failed_count), (1, 0))
        self.assertEqual(self.receiver.calls[before:], ["gale"])
        self.assertEqual(self._proposal("gale").get_operation_status(request_id)["delivery_state"], "accepted")

    def test_same_external_identity_is_isolated_between_gale_and_cloud(self) -> None:
        queued = self._queue_append("gale", "shared-cross-source-id")
        self._pull_once()
        cloud_receipt = self.receipts.list_pending_receipts()[0]
        gale_receipt, created = self.receipts.import_gale_submission(
            GaleReceiptImport(
                external_submission_id=queued["submission_uuid"],
                idempotency_key="shared-cross-source-id",
                title="Fictional Gale same identity",
                body="Fictional Gale body",
                location=NoteLocation("Step 11", "Followup"),
                tags=("fictional",),
            )
        )
        self.assertTrue(created)
        self.assertNotEqual(gale_receipt.id, cloud_receipt.id)
        self.assertEqual(
            {(item.source, item.external_submission_id) for item in self.receipts.list_pending_receipts()},
            {
                ("Gale", queued["submission_uuid"]),
                ("GreenCloud", queued["submission_uuid"]),
            },
        )


if __name__ == "__main__":
    unittest.main()
