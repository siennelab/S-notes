"""Focused Step 9 tests for the GreenCloud actor Reminder entrypoint."""

from __future__ import annotations

import importlib.util
import io
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import Mock, patch

from src.snotes.application.reminder_service import ReminderService
from src.snotes.domain.reminder_remote_contract import RemoteReminderError
from src.snotes.domain.reminders import ReminderDraft
from src.snotes.storage.repositories.reminders import SQLiteReminderRepository
from src.snotes.storage.schema import initialize_database
from src.snotes.transport.reminder_http import (
    ACTOR_ROLE,
    OWNER_ROLE,
    ReminderCredential,
    ReminderHttpApplication,
)


PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "src" / "snotes" / "transport"))
HELPER_PATH = (
    PROJECT_ROOT / "deploy" / "greencloud" / "ai-access" / "reminder_actor.py"
)
MODULE_NAME = "snotes_gc35_step09_reminder_actor"


def _load_helper():
    specification = importlib.util.spec_from_file_location(MODULE_NAME, HELPER_PATH)
    if specification is None or specification.loader is None:
        raise RuntimeError("step09_helper_import_unavailable")
    module = importlib.util.module_from_spec(specification)
    # dataclasses resolves postponed annotations through sys.modules while the
    # module is executing.
    sys.modules[MODULE_NAME] = module
    specification.loader.exec_module(module)
    return module


helper = _load_helper()


class _FakeReminderClient:
    instances: list["_FakeReminderClient"] = []

    def __init__(self, endpoint, credential_provider):
        self.endpoint = endpoint
        self.credential_provider = credential_provider
        self.closed = False
        self.command_calls: list[tuple[str, dict, str]] = []
        self.__class__.instances.append(self)

    def command(self, name: str, payload: dict) -> dict:
        token = self.credential_provider()
        self.command_calls.append((name, payload, token))
        return {"status": "ok", "token_seen": token}

    def close(self) -> None:
        self.closed = True


class _ApplicationReminderClient:
    """RemoteReminderClient-shaped adapter over the real HTTP application."""

    def __init__(self, application, endpoint, credential_provider):
        self.application = application
        self.endpoint = endpoint
        self.credential_provider = credential_provider
        self.closed = False

    def command(self, name: str, payload: dict) -> dict:
        # Match the production client's read-only schema preflight before POST.
        self._request("GET", "/v1/reminder/snapshot", None)
        return self._request("POST", f"/v1/reminder/commands/{name}", payload)

    def close(self) -> None:
        self.closed = True

    def _request(self, method: str, path: str, payload: dict | None) -> dict:
        import json

        response = self.application.handle(
            method,
            path,
            headers={"Authorization": f"Bearer {self.credential_provider()}"},
            body=b"" if payload is None else json.dumps(payload).encode("utf-8"),
        )
        if not 200 <= response.status < 300:
            raise RemoteReminderError(
                "auth" if response.status in {401, 403} else "server",
                code=response.body.get("error"),
                status=response.status,
            )
        return response.body


class GreenCloudActorReminderAccessTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory(prefix="snotes-step09-actor-")
        self.root = Path(self.temporary.name)
        self.uid = os.getuid()
        self.credential = self.root / "reminder-token"
        self.binding = helper.ActorAccess(
            self.uid,
            "claude-code",
            self.credential,
        )
        _FakeReminderClient.instances.clear()

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def _write_credential(self, value: str, *, mode: int = 0o400) -> None:
        if self.credential.exists() or self.credential.is_symlink():
            self.credential.unlink()
        self.credential.write_text(value, encoding="utf-8")
        self.credential.chmod(mode)

    def _dispatch(self, command: str = "list_reminder_labels", payload=None) -> dict:
        self._write_credential("fictional-actor-token")
        with (
            patch.object(helper.os, "geteuid", return_value=self.uid),
            patch.dict(helper.UID_BINDINGS, {self.uid: self.binding}, clear=True),
            patch.object(helper, "RemoteReminderClient", _FakeReminderClient),
        ):
            return helper.dispatch(
                {"command": command, "payload": {} if payload is None else payload}
            )

    def test_only_two_production_uids_have_fixed_actor_and_credential_paths(self) -> None:
        self.assertEqual(set(helper.UID_BINDINGS), {2001, 2002})
        claude = helper.binding_for_effective_uid(2001)
        clawbot = helper.binding_for_effective_uid(2002)
        self.assertEqual(
            (claude.uid, claude.actor, claude.credential_path),
            (
                2001,
                "claude-code",
                Path("/srv/snotes/actor-credentials/claude-code/reminder-token"),
            ),
        )
        self.assertEqual(
            (clawbot.uid, clawbot.actor, clawbot.credential_path),
            (
                2002,
                "clawbot",
                Path("/srv/snotes/actor-credentials/clawbot/reminder-token"),
            ),
        )
        for uid in (0, 2003):
            with self.subTest(uid=uid), self.assertRaisesRegex(
                ValueError, "unsupported_os_identity"
            ):
                helper.binding_for_effective_uid(uid)

    def test_dispatch_rejects_root_and_unknown_os_identities(self) -> None:
        for uid in (0, 2003):
            with self.subTest(uid=uid), patch.object(
                helper.os, "geteuid", return_value=uid
            ), self.assertRaisesRegex(ValueError, "unsupported_os_identity"):
                helper.dispatch({"command": "list_reminder_labels", "payload": {}})

    def test_reminder_allowlist_is_the_existing_actor_surface_only(self) -> None:
        self.assertEqual(
            helper.ACTOR_COMMANDS,
            frozenset(
                {
                    "list_reminder_labels",
                    "list_reminder_tables",
                    "get_reminder_table",
                    "list_reminder_items",
                    "get_reminder_item",
                    "create_reminder_label",
                    "create_reminder_table",
                    "update_reminder_table",
                    "create_reminder_item",
                    "update_reminder_item",
                    "complete_reminder_item",
                    "pause_reminder_item",
                    "resume_reminder_item",
                }
            ),
        )
        for command in (
            "delete_reminder",
            "delete_table",
            "set_table_ai_visible",
            "save_settings",
            "list_ai_operation_logs",
        ):
            with self.subTest(command=command), self.assertRaisesRegex(
                ValueError, "command_forbidden"
            ):
                self._dispatch(command)

    def test_request_cannot_override_actor_endpoint_or_credential_path(self) -> None:
        invalid_requests = (
            {
                "command": "list_reminder_labels",
                "payload": {},
                "actor": "clawbot",
            },
            {
                "command": "list_reminder_labels",
                "payload": {},
                "endpoint": "https://example.invalid",
            },
            {
                "command": "list_reminder_labels",
                "payload": {},
                "credential_path": str(self.root / "other"),
            },
        )
        with patch.object(helper.os, "geteuid", return_value=self.uid), patch.dict(
            helper.UID_BINDINGS, {self.uid: self.binding}, clear=True
        ):
            for request in invalid_requests:
                with self.subTest(request=set(request)), self.assertRaisesRegex(
                    ValueError, "request_invalid"
                ):
                    helper.dispatch(request)

        result = self._dispatch(payload={"query": "fictional metadata"})
        self.assertEqual(result["status"], "ok")
        client = _FakeReminderClient.instances[-1]
        self.assertEqual(client.endpoint, "http://127.0.0.1:8766")
        self.assertEqual(
            client.command_calls,
            [
                (
                    "list_reminder_labels",
                    {"query": "fictional metadata"},
                    "fictional-actor-token",
                )
            ],
        )
        self.assertTrue(client.closed)

    def test_credential_requires_matching_euid_owner_and_exact_0400_mode(self) -> None:
        self._write_credential("fictional-token")
        with patch.object(helper.os, "geteuid", return_value=self.uid + 1):
            with self.assertRaisesRegex(ValueError, "credential_identity_mismatch"):
                helper.read_credential(self.binding)

        self.credential.chmod(0o600)
        with patch.object(helper.os, "geteuid", return_value=self.uid):
            with self.assertRaisesRegex(ValueError, "credential_permissions_invalid"):
                helper.read_credential(self.binding)

        self.credential.chmod(0o400)
        real_fstat = os.fstat

        def wrong_owner(descriptor: int):
            values = list(real_fstat(descriptor))
            values[4] = self.uid + 1
            return os.stat_result(values)

        with (
            patch.object(helper.os, "geteuid", return_value=self.uid),
            patch.object(helper.os, "fstat", side_effect=wrong_owner),
            self.assertRaisesRegex(ValueError, "credential_permissions_invalid"),
        ):
            helper.read_credential(self.binding)

    def test_credential_rejects_symlink_large_empty_and_whitespace_tokens(self) -> None:
        target = self.root / "real-token"
        target.write_text("fictional-token", encoding="utf-8")
        target.chmod(0o400)
        self.credential.symlink_to(target)
        with patch.object(helper.os, "geteuid", return_value=self.uid):
            with self.assertRaises(OSError):
                helper.read_credential(self.binding)

        self.credential.unlink()
        for value, expected in (
            ("x" * 4097, "credential_permissions_invalid"),
            ("", "credential_invalid"),
            ("fictional token", "credential_invalid"),
            ("fictional-token\nsecond", "credential_invalid"),
        ):
            with self.subTest(size=len(value), whitespace=any(c.isspace() for c in value)):
                self._write_credential(value)
                with patch.object(helper.os, "geteuid", return_value=self.uid):
                    with self.assertRaisesRegex(ValueError, expected):
                        helper.read_credential(self.binding)

    def test_each_dispatch_rereads_credential_and_closes_its_client(self) -> None:
        with (
            patch.object(helper.os, "geteuid", return_value=self.uid),
            patch.dict(helper.UID_BINDINGS, {self.uid: self.binding}, clear=True),
            patch.object(helper, "RemoteReminderClient", _FakeReminderClient),
        ):
            self._write_credential("fictional-token-one")
            first = helper.dispatch(
                {"command": "list_reminder_labels", "payload": {}}
            )
            self._write_credential("fictional-token-two")
            second = helper.dispatch(
                {"command": "list_reminder_labels", "payload": {}}
            )

        self.assertEqual(first["token_seen"], "fictional-token-one")
        self.assertEqual(second["token_seen"], "fictional-token-two")
        self.assertEqual(len(_FakeReminderClient.instances), 2)
        self.assertTrue(all(client.closed for client in _FakeReminderClient.instances))

    def test_client_is_closed_when_command_raises(self) -> None:
        self._write_credential("fictional-token")

        class FailingClient(_FakeReminderClient):
            def command(self, name: str, payload: dict) -> dict:
                self.credential_provider()
                raise RuntimeError("fictional_failure")

        with (
            patch.object(helper.os, "geteuid", return_value=self.uid),
            patch.dict(helper.UID_BINDINGS, {self.uid: self.binding}, clear=True),
            patch.object(helper, "RemoteReminderClient", FailingClient),
            self.assertRaisesRegex(RuntimeError, "fictional_failure"),
        ):
            helper.dispatch({"command": "list_reminder_labels", "payload": {}})
        self.assertTrue(FailingClient.instances[-1].closed)

    def test_main_rejects_unsafe_inputs_with_only_the_safe_error(self) -> None:
        marker = "FICTIONAL_SECRET_MUST_NOT_ECHO"
        cases = (
            (
                "request_too_large",
                2001,
                [str(HELPER_PATH)],
                (marker.encode("utf-8") + b"x" * helper.MAX_REQUEST_BYTES)[
                    : helper.MAX_REQUEST_BYTES + 1
                ],
            ),
            (
                "invalid_json",
                2001,
                [str(HELPER_PATH)],
                f'{{"secret":"{marker}"'.encode("utf-8"),
            ),
            (
                "extra_argv",
                2001,
                [str(HELPER_PATH), marker],
                b'{"command":"list_reminder_labels","payload":{}}',
            ),
            (
                "root",
                0,
                [str(HELPER_PATH)],
                f'{{"secret":"{marker}"}}'.encode("utf-8"),
            ),
        )
        for name, uid, argv, raw in cases:
            with self.subTest(case=name):
                input_stream = io.TextIOWrapper(io.BytesIO(raw), encoding="utf-8")
                output_stream = io.StringIO()
                client = Mock(side_effect=AssertionError("client_must_not_start"))
                with (
                    patch.object(helper.os, "geteuid", return_value=uid),
                    patch.object(helper.sys, "argv", argv),
                    patch.object(helper.sys, "stdin", input_stream),
                    patch.object(helper.sys, "stdout", output_stream),
                    patch.object(helper, "RemoteReminderClient", client),
                ):
                    self.assertEqual(helper.main(), 2)
                emitted = output_stream.getvalue()
                self.assertEqual(
                    json.loads(emitted),
                    {"status": "failed", "reason": "actor_request_rejected"},
                )
                self.assertNotIn(marker, emitted)
                client.assert_not_called()


class GreenCloudActorReminderIntegrationTests(unittest.TestCase):
    """Exercise both Cloud actors against a real loopback API and temporary DB."""

    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory(prefix="snotes-step09-api-")
        self.root = Path(self.temporary.name)
        self.home = self.root / "home"
        self.home.mkdir(mode=0o700)
        self.environment = patch.dict(os.environ, {"HOME": str(self.home)})
        self.environment.start()

        connections = initialize_database(self.root / "reminder.db")
        self.service = ReminderService(SQLiteReminderRepository(connections))
        self.visible_table = self.service.create_table(
            "FICTIONAL Step 9 visible",
            ai_visible=True,
        )
        self.hidden_table = self.service.create_table(
            "FICTIONAL Step 9 hidden",
            ai_visible=False,
        )
        self.hidden_item = self.service.create_reminder(
            ReminderDraft(
                title="FICTIONAL Step 9 hidden item",
                lifecycle="active",
                action_spec={"type": "one_time", "date": "2030-01-01"},
                note="FICTIONAL metadata only",
                table_id=self.hidden_table.id,
            )
        )
        self.tokens = {
            "claude-code": "fictional-step09-claude-token",
            "clawbot": "fictional-step09-clawbot-token",
        }
        credentials = {
            "fictional-step09-owner-token": ReminderCredential("owner", OWNER_ROLE),
            **{
                token: ReminderCredential(actor, ACTOR_ROLE)
                for actor, token in self.tokens.items()
            },
        }
        self.application = ReminderHttpApplication(self.service, credentials)
        self.endpoint = helper.ENDPOINT

    def tearDown(self) -> None:
        self.environment.stop()
        self.temporary.cleanup()

    def _actor_binding(self, actor: str) -> tuple[object, str]:
        token_path = self.root / actor / "reminder-token"
        token_path.parent.mkdir(mode=0o700)
        token_path.write_text(self.tokens[actor], encoding="utf-8")
        token_path.chmod(0o400)
        return helper.ActorAccess(os.getuid(), actor, token_path), self.tokens[actor]

    def _helper_dispatch(self, binding, command: str, payload: dict) -> dict:
        def build_client(endpoint, provider):
            return _ApplicationReminderClient(self.application, endpoint, provider)

        with (
            patch.object(helper.os, "geteuid", return_value=os.getuid()),
            patch.dict(helper.UID_BINDINGS, {os.getuid(): binding}, clear=True),
            patch.object(helper, "RemoteReminderClient", side_effect=build_client),
        ):
            return helper.dispatch({"command": command, "payload": payload})

    def test_both_cloud_actors_keep_fixed_identity_visibility_and_owner_boundary(self) -> None:
        for actor in ("claude-code", "clawbot"):
            with self.subTest(actor=actor):
                binding, token = self._actor_binding(actor)
                request_id = f"step09-{actor}-create"
                created = self._helper_dispatch(
                    binding,
                    "create_reminder_item",
                    {
                        "request_id": request_id,
                        "table_id": self.visible_table.id,
                        "title": f"FICTIONAL Step 9 {actor}",
                        "action_spec": {
                            "type": "one_time",
                            "date": "2030-02-01",
                        },
                    },
                )
                self.assertEqual(created["status"], "created")
                operation = next(
                    row
                    for row in self.service.list_ai_operation_logs().rows
                    if row.request_id == request_id
                )
                self.assertEqual(operation.actor_target_id, actor)

                hidden_table = self._helper_dispatch(
                    binding,
                    "get_reminder_table",
                    {"table_id": self.hidden_table.id},
                )
                hidden_item = self._helper_dispatch(
                    binding,
                    "get_reminder_item",
                    {"item_id": self.hidden_item.id},
                )
                self.assertEqual(hidden_table["status"], "not_found")
                self.assertEqual(hidden_item["status"], "not_found")

                client = _ApplicationReminderClient(
                    self.application,
                    self.endpoint,
                    lambda: token,
                )
                try:
                    for command, payload in (
                        (
                            "set_table_ai_visible",
                            {"table_id": self.hidden_table.id, "ai_visible": True},
                        ),
                        ("delete_reminder", {"item_id": created["item_id"]}),
                    ):
                        with self.subTest(actor=actor, command=command), self.assertRaises(
                            RemoteReminderError
                        ) as raised:
                            client.command(command, payload)
                        self.assertEqual(raised.exception.status, 403)
                        self.assertEqual(raised.exception.code, "command_forbidden")
                finally:
                    client.close()

                for injected, expected_code in (
                    ({"actor_target_id": "owner"}, "identity_in_body_forbidden"),
                    ({"actor_targets": ["owner"]}, "unknown_fields:actor_targets"),
                ):
                    payload = {
                        "request_id": f"step09-{actor}-{next(iter(injected))}",
                        "table_id": self.visible_table.id,
                        "title": "FICTIONAL injection rejected",
                        "action_spec": {
                            "type": "one_time",
                            "date": "2030-03-01",
                        },
                        **injected,
                    }
                    with self.subTest(actor=actor, injected=set(injected)), self.assertRaises(
                        RemoteReminderError
                    ) as raised:
                        self._helper_dispatch(
                            binding,
                            "create_reminder_item",
                            payload,
                        )
                    self.assertEqual(raised.exception.status, 400)
                    self.assertEqual(raised.exception.code, expected_code)


if __name__ == "__main__":
    unittest.main()
