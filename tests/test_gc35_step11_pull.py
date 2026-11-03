from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from subprocess import CompletedProcess
from types import SimpleNamespace
from unittest.mock import Mock, patch

import gale_production_pull_helper

from src.snotes.application.gale_service import (
    GaleControlService,
    GreenCloudPullService,
)
from src.snotes.application.greencloud_receipt_service import GreenCloudFollowupResult
from src.snotes.domain.gale import GalePullResult, GalePublishResult
from src.snotes.domain.greencloud import (
    GreenCloudError,
    GreenCloudPullResult,
    GreenCloudSubmission,
    MultiSourcePullResult,
)
from src.snotes.transport.greencloud.outbox import (
    SystemGreenCloudOutboxTransport,
    parse_export_payload,
)


UUID = "00000000-0000-4000-8000-000000000611"
VERSION = "snotes-v1-" + "a" * 64


def proposal(**changes) -> dict:
    value = {
        "submission_uuid": UUID,
        "source_actor": "claude-code",
        "request_id": "fictional-step11-request",
        "operation_type": "append",
        "note_id": "42",
        "expected_version": VERSION,
        "payload": {"content": "Fictional tail"},
        "created_at": "2026-10-04T00:00:00Z",
        "delivery_state": "delivered",
        "delivered_at": "2026-10-04T00:01:00Z",
    }
    value.update(changes)
    return value


def export_payload(*items: dict) -> bytes:
    return json.dumps(
        {
            "status": "ok",
            "source": "greencloud",
            "schema_version": 3,
            "submissions": list(items),
        }
    ).encode()


class FakeAudit:
    def __init__(self) -> None:
        self.events: list[tuple[tuple, dict]] = []

    def record_event(self, *args, **kwargs) -> None:
        self.events.append((args, kwargs))


class FakeGreenCloudTransport:
    def __init__(self, submissions=()) -> None:
        self.submissions = tuple(submissions)
        self.acks: list[tuple[str, str]] = []
        self.fail_ack = False

    def export_unacknowledged(self):
        return self.submissions

    def acknowledge(self, submission_uuid: str, receipt_id: str) -> None:
        if self.fail_ack:
            self.fail_ack = False
            raise RuntimeError("fictional cloud outage")
        self.acks.append((submission_uuid, receipt_id))


class FakeReceipts:
    def __init__(self) -> None:
        self.items: dict[str, SimpleNamespace] = {}
        self.fail = False

    def import_greencloud_pending(self, submission):
        if self.fail:
            raise RuntimeError("fictional local transaction failure")
        existing = self.items.get(submission.submission_uuid)
        if existing is not None:
            return existing, False
        item = SimpleNamespace(id=len(self.items) + 51)
        self.items[submission.submission_uuid] = item
        return item, True


class FakeSettings:
    def __init__(self) -> None:
        self.values: dict[str, object] = {}

    def get_bool(self, key: str, default: bool) -> bool:
        return bool(self.values.get(key, default))

    def set_bool(self, key: str, value: bool) -> None:
        self.values[key] = value

    def get_text(self, key: str, default: str = "") -> str:
        return str(self.values.get(key, default))

    def set_text(self, key: str, value: str) -> None:
        self.values[key] = value


class PullStub:
    def __init__(self, result=None, error: Exception | None = None) -> None:
        self.result = result
        self.error = error
        self.calls = 0

    def pull_once(self):
        self.calls += 1
        if self.error is not None:
            raise self.error
        return self.result


class PublishStub:
    def publish_automatic(self):
        return GalePublishResult("duplicate", "", 0)

    def publish_manual(self):
        return GalePublishResult("duplicate", "", 0)


class GC35Step11PullTest(unittest.TestCase):
    def test_greencloud_gale_preserves_source_without_oracle_alias(self) -> None:
        parsed = parse_export_payload(export_payload(proposal(source_actor="gale")))
        self.assertEqual(parsed[0].source_actor, "gale")

    def test_parser_preserves_cloud_actor_and_operation_envelope(self) -> None:
        parsed = parse_export_payload(export_payload(proposal()))
        self.assertEqual(
            parsed,
            (
                GreenCloudSubmission(
                    UUID,
                    "claude-code",
                    "fictional-step11-request",
                    "append",
                    42,
                    VERSION,
                    {"content": "Fictional tail"},
                    "2026-10-04T00:00:00Z",
                    "delivered",
                    "2026-10-04T00:01:00Z",
                ),
            ),
        )

    def test_parser_rejects_identity_operation_and_envelope_confusion(self) -> None:
        cases = (
            (proposal(source_actor="unknown-actor"), "source_actor_invalid"),
            (proposal(source_actor="s"), "source_actor_invalid"),
            (proposal(operation_type="delete"), "operation_type_invalid"),
            (proposal(note_id=None), "operation_envelope_invalid"),
            (proposal(delivery_state="acknowledged"), "remote_delivery_state_invalid"),
            (proposal(unexpected=True), "remote_submission_invalid"),
        )
        for item, reason in cases:
            with self.subTest(reason=reason), self.assertRaisesRegex(
                GreenCloudError, reason
            ):
                parse_export_payload(export_payload(item))

    def test_ssh_adapter_uses_fixed_commands_and_existing_restricted_key(self) -> None:
        with tempfile.TemporaryDirectory(dir="/private/tmp") as temporary:
            identity = Path(temporary) / "mirror-key"
            identity.write_text("fictional", encoding="utf-8")
            identity.chmod(0o600)
            calls: list[tuple[list[str], dict]] = []

            def fake_run(command, **kwargs):
                calls.append((command, kwargs))
                action = command[-1]
                request = json.loads(kwargs["input"])
                if action == "notes-outbox-export":
                    self.assertEqual(request, {})
                    stdout = export_payload(proposal())
                elif action == "notes-outbox-acknowledge":
                    self.assertEqual(request["local_receipt_id"], "51")
                    stdout = json.dumps(
                        {"status": "ok", "submission_uuid": UUID}
                    ).encode()
                else:
                    self.assertEqual(action, "notes-outbox-receipt")
                    self.assertEqual(request["terminal_state"], "accepted")
                    stdout = json.dumps(
                        {
                            "status": "ok",
                            "submission_uuid": UUID,
                            "request_id": "fictional-step11-request",
                            "delivery_state": "accepted",
                        }
                    ).encode()
                return CompletedProcess(command, 0, stdout, b"")

            transport = SystemGreenCloudOutboxTransport(identity_file=identity)
            with patch("subprocess.run", side_effect=fake_run):
                self.assertEqual(len(transport.export_unacknowledged()), 1)
                transport.acknowledge(UUID, "51")
                transport.write_terminal_receipt(
                    "claude-code",
                    "fictional-step11-request",
                    "accepted",
                    {"note_id": 42},
                )
            self.assertEqual(
                [call[0][-1] for call in calls],
                [
                    "notes-outbox-export",
                    "notes-outbox-acknowledge",
                    "notes-outbox-receipt",
                ],
            )
            for command, _kwargs in calls:
                self.assertIn("IdentitiesOnly=yes", command)
                self.assertIn("StrictHostKeyChecking=yes", command)
                self.assertEqual(command[-2], "snotes-publish@optimal")
                self.assertNotIn("sudo", command)

    def test_pull_imports_before_ack_and_ack_retry_does_not_duplicate(self) -> None:
        submission = parse_export_payload(export_payload(proposal()))[0]
        transport = FakeGreenCloudTransport((submission,))
        receipts = FakeReceipts()
        service = GreenCloudPullService(transport, receipts, FakeAudit())
        transport.fail_ack = True
        with self.assertRaisesRegex(GreenCloudError, "ack_failed"):
            service.pull_once()
        self.assertEqual(transport.acks, [])
        retried = service.pull_once()
        self.assertEqual(
            retried,
            GreenCloudPullResult(1, 0, 1, 1),
        )
        self.assertEqual(transport.acks, [(UUID, "51")])

    def test_local_import_failure_never_acknowledges_cloud(self) -> None:
        submission = parse_export_payload(export_payload(proposal()))[0]
        transport = FakeGreenCloudTransport((submission,))
        receipts = FakeReceipts()
        receipts.fail = True
        with self.assertRaisesRegex(RuntimeError, "local transaction"):
            GreenCloudPullService(transport, receipts, FakeAudit()).pull_once()
        self.assertEqual(transport.acks, [])

    def test_control_attempts_greencloud_and_followup_when_gale_is_offline(self) -> None:
        gale = PullStub(error=RuntimeError("fictional Oracle outage"))
        greencloud_result = GreenCloudPullResult(1, 1, 1, 0)
        cloud = PullStub(result=greencloud_result)
        followups: list[str] = []
        settings = FakeSettings()
        control = GaleControlService(
            PublishStub(),
            gale,
            settings,
            greencloud_pull=cloud,
            greencloud_followup=lambda: followups.append("flushed"),
            clock=lambda: 1_000.0,
        )
        result = control.check_submissions_now()
        self.assertEqual(
            result,
            MultiSourcePullResult(None, greencloud_result, ("gale",)),
        )
        self.assertEqual((gale.calls, cloud.calls, followups), (1, 1, ["flushed"]))
        self.assertTrue(control.last_completed_pull_at())

    def test_gale_only_control_retains_original_result_and_exception(self) -> None:
        expected = GalePullResult(0, 0, 0, 0)
        settings = FakeSettings()
        control = GaleControlService(PublishStub(), PullStub(result=expected), settings)
        self.assertIs(control.check_submissions_now(), expected)
        failing = GaleControlService(
            PublishStub(), PullStub(error=RuntimeError("fictional")), FakeSettings()
        )
        with self.assertRaisesRegex(RuntimeError, "fictional"):
            failing.check_submissions_now()

    def test_control_consumes_followup_failures_and_preserves_raw_gui_result(self) -> None:
        for failed_count in (0, 2):
            with self.subTest(failed_count=failed_count):
                followup_result = GreenCloudFollowupResult(
                    2, 2 - failed_count, 0, failed_count
                )
                followup = Mock(return_value=followup_result)
                gale_result = GalePullResult(0, 0, 0, 0)
                cloud_result = GreenCloudPullResult(0, 0, 0, 0)
                control = GaleControlService(
                    PublishStub(), PullStub(result=gale_result), FakeSettings(),
                    greencloud_pull=PullStub(result=cloud_result),
                    greencloud_followup=followup,
                )
                self.assertEqual(
                    control.check_submissions_now(),
                    MultiSourcePullResult(
                        gale_result, cloud_result,
                        ("greencloud_followup",) if failed_count else (),
                    ),
                )
                followup.assert_called_once_with()
                self.assertIs(control.flush_greencloud_decisions(), followup_result)

    def test_scheduled_followup_reports_failure_without_auto_pull(self) -> None:
        cases = ((0, None), (2, None), (0, RuntimeError("fictional")))
        for failed_count, error in cases:
            with self.subTest(failed_count=failed_count, error=error):
                gale = PullStub(result=GalePullResult(0, 0, 0, 0))
                cloud = PullStub(result=GreenCloudPullResult(0, 0, 0, 0))
                followup = Mock(
                    return_value=GreenCloudFollowupResult(
                        2, 2 - failed_count, 0, failed_count
                    ),
                    side_effect=error,
                )
                publish = PublishStub()
                publish.publish_automatic = Mock(wraps=publish.publish_automatic)
                control = GaleControlService(
                    publish, gale, FakeSettings(),
                    greencloud_pull=cloud, greencloud_followup=followup,
                )
                control.set_auto_pull_enabled(False)
                control.set_auto_publish_enabled(True)
                pull_result, publish_result = control.scheduled_once()
                self.assertEqual(
                    pull_result,
                    MultiSourcePullResult(None, None, ("greencloud_followup",))
                    if failed_count or error else None,
                )
                self.assertEqual((gale.calls, cloud.calls), (0, 0))
                followup.assert_called_once_with()
                publish.publish_automatic.assert_called_once_with()
                self.assertEqual(publish_result.status, "duplicate")
                self.assertEqual(control.last_completed_pull_at(), "")

    def test_helper_exit_reflects_followup_results_for_both_pull_settings(self) -> None:
        for scheduled in (False, True):
            for auto_pull in (False, True):
                for failed_count in (0, 2):
                    with self.subTest(
                        scheduled=scheduled,
                        auto_pull=auto_pull,
                        failed_count=failed_count,
                    ):
                        followup = Mock(return_value=GreenCloudFollowupResult(
                            2, 2 - failed_count, 0, failed_count
                        ))
                        control = GaleControlService(
                            PublishStub(), PullStub(result=GalePullResult(0, 0, 0, 0)),
                            FakeSettings(),
                            greencloud_pull=PullStub(
                                result=GreenCloudPullResult(0, 0, 0, 0)
                            ),
                            greencloud_followup=followup,
                        )
                        control.set_auto_pull_enabled(auto_pull)
                        with patch.object(
                            gale_production_pull_helper, "build_gale_control_service",
                            return_value=control,
                        ):
                            code = gale_production_pull_helper.main(
                                ["--scheduled"] if scheduled else []
                            )
                        self.assertEqual(code, 2 if failed_count else 0)
                        followup.assert_called_once_with()

    def test_scheduled_followup_retries_when_auto_pull_is_disabled(self) -> None:
        followups: list[str] = []
        settings = FakeSettings()
        settings.set_bool(GaleControlService.AUTO_PULL_KEY, False)
        control = GaleControlService(
            PublishStub(),
            PullStub(result=GalePullResult(0, 0, 0, 0)),
            settings,
            greencloud_pull=PullStub(result=GreenCloudPullResult(0, 0, 0, 0)),
            greencloud_followup=lambda: followups.append("flushed"),
        )
        pull_result, _publish_result = control.scheduled_once()
        self.assertIsNone(pull_result)
        self.assertEqual(followups, ["flushed"])


if __name__ == "__main__":
    unittest.main()
